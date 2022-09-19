#基本的shell脚本编写演示
vim hello.sh //建立hello这个shell文件
//进入shell文件，按“i”键进入Insert模式
//输入shell规则中的开头行"#!/bin/bash/",
//学名是“Shebang”,它指的是出现在文本文件的第一行前两个字符#！
//它的作用就是告诉计算机用哪个解释器去读
//·以"#!/bin/sh"开头的文件，程序执行的时候就会调用/bin/sh，也就是bash解释器
//ls -l /bin/sh,打开后可以看到。蓝色的/bin/sh -> bash //也就是说它是指向bash的一个软连接
//我的竟然是lrwxrwxrwx 1 root root 4 Jul 19  2019 /bin/sh -> dash //后面测试没问题，不影响通过/bin/sh使用bash
//如果在linux环境下去建立一个python解释器去执行的代码，就以#!/usr/bin/python开头
//yum是一个用python解释器来实现的。我的电脑里没有，which，＋空格，yum
//安装yum 
//首先
//先更新一波
//agt update
//vim
//apt install vim
//wget
//apt install wget
//wget http://yum.baseurl.org/download/3.2/yum-3.2.28.tar.gz
//tar xvf yum-3.2.28.tar.gz
//cd yum-3.2.28
//yummain.py install yum  //我的要这样运行python3.8 yummain.py install yum
#————————————————————————————————————————————————————————————————————安装失败啦，后续步骤后不对
//yum
//apt install yum
//ifconfig
//apt install net-tools
//ping
//apt install iputils-ping

root@84a6e2fee621:~/.jupyter# yum version
Installed: $releasever/x86_64                                               0:da39a3ee5e6b4b0d3255bfef95601890afd80709
version
#————————————————————————————————————————————————————————————————————————————————
//env是一个查看linux环境变量的一个命令，后面接上一个解释器，#!/usr/bin/env是一个在不同平台上都能正确找到解释器的方法
//写好ehco，＋空格，"我是shell脚本的一行代码"保存退出
#——————————————————————————————————————————————————————————————这里是hello.sh

#!/bin/bash
//bash是执行我们的shell命令的
//第二行，可以写上注释“这是我们的第一个Shell脚本”
#这是我们的第一个Shell脚本

echo "我是shell脚本的一行代码"
#____________________________________________________________________________
//用linux命令
//像直接在命令行里输入hello.sh，它都会直接去PATH里去寻找，这样没有这个东西，就会返回：-bash：hello.sh:未找到命令
//而我们使用相对路径的方式，“./hello.sh”找到这个文件去执行它呢，会提示-bash：hello.sh:权限不够
//这时我们指明解释器“/bin/bash”，＋空格，hello.sh去执行，会发现正确的执行了它
//whereis，＋空格，echo查看位置
//使用which，＋空格，bash可以看到我的“/usr/bin/bash”
//使用/usr/bin/bash，＋空格，hello.sh去执行，成功执行
//通过chmod,，＋空格，+x，＋空格，hello.sh给加一个权限，这个时候执行./hello.sh就会成功执行
//用$SHELL就是输入这个变量值，echo，＋空格，$SHELL,就能得到/bin/bash这个变量值
//再写一个hello.py,同样加上权限。./hello.py不能运行,错误是：未预期的符号，附近有语法错，因为是bash解释器去解释python文件
#__________________________________________________________________这里是hello.py

#!/usr/bin/bash

#coding:utf-8  //写中文时，加这个，我的docker不能写中文,就没加这句

print("This is a .py file!")

//如果指定的解释器不对，那么指定的解释器会被忽略，转而交给SHELL解释器
#________________________________________________________________________________