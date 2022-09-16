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
from pycocotools.coco import COCO
import numpy as np
import skimage.io as io
import matplotlib.pyplot as plt
import os
import shutil
import cv2
import os
import re
from glob import glob

import argparse

def rr_Raw_label_rename():#读取重命名后的文件夹Raw_label_rename，将其中不存在于old_hw的内容重新复制回未处理的标签文件夹data2_path下
 
    parser = argparse.ArgumentParser(description="读取重命名后的文件夹Raw_label_rename,将其中不存在于old_hw的内容重新复制回未处理的标签文件夹data2_path下")
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath('read_yaml0907.py'))))
    from f3.read_yaml0907 import read_yaml
    args = read_yaml('default.yaml')
    parser.add_argument('--data4_path',type=str,default=args['data4_path']['default'],help='读取20220824下载的新标签')
    parser.add_argument('--Raw_label_rename_path',type=str,default=args['Raw_label_rename_path']['default'],help='重命名后放在Raw_label_rename文件夹')
    parser.add_argument('--data3_path',type=str,default=args['data3_path']['default'],help='之前下载的label')
    args = parser.parse_args()
    data3_path=args.data3_path
    # old_hw()#读取最开始的标签,新生成的Raw_label_rename/2022****/就是下一次的old_hw
    data4_path=args.data4_path
    Raw_label_rename_path=args.Raw_label_rename_path
    if not os.path.exists(data4_path):#rename_new_label()最后一步的时候删除了data2_path文件夹
        os.makedirs(data4_path)

    file3_list=os.listdir(Raw_label_rename_path)
    print(file3_list)
    #读取最开始的标签os.listdir(data3_path)
    file1_list= os.listdir(data3_path)#随机[...'hw101.jilin-ai.com_35_D20220724220000_20220724235959-0000158490.json', 'hw101.jilin-ai.com_35_D20220724220000_20220724235959-0000149310.json']

    for file in file3_list:
        if file in file1_list:
            print(file,"已存在于old_hw")
        else:
            shutil.copy(os.path.join(Raw_label_rename_path,file),data4_path)#将命名后文件重新复制回Raw_label路径下
            print('已复制',file,'到Raw_label下')
            shutil.copy(os.path.join(Raw_label_rename_path,file),data3_path)#将命名后文件重新复制到old_hw路径下
            print('已复制',file,'到old_hw下')

rr_Raw_label_rename()#将存储在指定位置的标签，做筛选后，重新存放回Raw_label路径下