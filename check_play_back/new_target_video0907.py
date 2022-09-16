#导入模块
import os,sys                       
import os
from re import L
import numpy as np
from shapely.geometry import Polygon
from PIL import Image
from tqdm import tqdm
import json
import pdb

import numpy as np
import skimage.io as io
# import matplotlib.pyplot as plt
import os
import shutil
# from pycocotools.coco import COCO
from pathlib import Path
import glob

import argparse


def new_target_video():
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath('read_yaml0907.py'))))
    from f3.read_yaml0907 import read_yaml 
    args = read_yaml('default.yaml')
    parser = argparse.ArgumentParser(description="将CORE中不存在于old(data1_path)中的play_back(dat2_path)文件,存储在target(determiantion)中,并更新Old文件夹内容")

    parser.add_argument('--determination1',type=str,default=args['determination1']['default'],help='选出的新视频存放路径')
    parser.add_argument('--data1_path',type=str,default=args['data1_path']['default'],help='以所有处理过的视频名命名的空文件夹路径 (type=str)')
    parser.add_argument('--data2_path',type=str,default=args['data2_path']['default'],help='待筛查的类型文件路径（未成功脱敏、缺失状态、无护工图片现象、其它）')

    args = parser.parse_args()
    determination = args.determination1
    data1_path = args.data1_path
    data2_path = args.data2_path

    if not os.path.exists(determination):
        os.makedirs(determination)
    if not os.path.exists(data1_path):
        os.makedirs(data1_path)
    if not os.path.exists(data2_path):
        os.makedirs(data2_path)
    file1_list= os.listdir(data1_path)
    folders=glob.glob(data2_path+'/**',recursive=True)#循环遍历
    num=0
    for folder in folders:
        file=folder.rsplit('/',1)[1]#取出folders中某个的'/'后file
        if len(file.rsplit('.',1))>1:
            num=num+1
            filename=file.rsplit('.',1)[0]#取file的名filename#'hw101.jilin-ai.com_33_D20220731175900_20220731180500'
            if filename in file1_list:
                print('已处理文件--',str(num)+file)
            else:
                deter = determination +'/'+ str(file)#目标路径
                old_newnakedir=data1_path + '/' + str(filename)#在old文件夹中存档
                source=folder #+ '/' + str(file)#源路径
                print('已复制',source,'文件--',str(num),"为"+deter+"------old已新添加",old_newnakedir)
                shutil.copyfile(source, deter)#复制到targrt文件夹
                os.makedirs(old_newnakedir,mode=0o777)
        else:
            continue
    print("文件总数为——————————",num)



new_target_video()

