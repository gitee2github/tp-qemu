#include <linux/init.h>
#include <linux/module.h>
#include <linux/jiffies.h>

static int __init hello_init(void)
{
	unsigned long current_i, stamp_30;
	printk(KERN_INFO "insmod deadlock_test!!!\n");
	current_i = jiffies;
	stamp_30 = current_i + 30*HZ;
	printk(KERN_INFO "%lu\n", current_i);
	printk(KERN_INFO "%lu\n", stamp_30);
	local_irq_disable();
	while(current_i != stamp_30)
	{
		current_i = jiffies;
	}
	printk(KERN_INFO "30s is over!!!\n");
	return 0;
}

module_init(hello_init);

static void __exit hello_exit(void)
{
	printk(KERN_INFO "deadlockup test exit\n");
}
module_exit(hello_exit);

MODULE_AUTHOR("zhk <zhuhuankai1@huawei.com>");
MODULE_LICENSE("GPL v2");
MODULE_DESCRIPTION("deadlockup test module");
MODULE_ALIAS("a simplest module");
