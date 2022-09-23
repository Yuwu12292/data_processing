#!/usr/bin/bash
 
echo "开始测试......"
# echo "进入make_coco路径,可以进行制作当天coco文件........"
# cd make_coco
# # python /home/yckj2334/sh_batch/python1.py
# dir
# python3.8 make_coco_0907.py -h 
# wait
# echo "执行结束，退回主路径"
# cd ..
dir
python3.8 test.py -h
wait
# python /home/yckj2334/sh_batch/python2.py
# wait
# python /home/yckj2334/sh_batch/python3.py
 
echo "结束测试......"
 
 
 
 
#wait能等待前一个脚本执行完毕，再执行下一个条命令；
#若需要批量不指定执行顺序，则将执行命令放在同一wait区域内即可