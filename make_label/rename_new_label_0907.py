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

def rename_new_label():#定义给新标签重命名的函数 
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath('read_yaml0907.py'))))
    from f3.read_yaml0907 import read_yaml
    args = read_yaml('default.yaml')
    parser = argparse.ArgumentParser(description="将需要重命名标签与名称正确标签存储在一起，存在指定位置")
    parser.add_argument('--data4_path',type=str,default=args['data4_path']['default'],help='读取20220824下载的新标签')
    parser.add_argument('--Raw_label_rename_path',type=str,default=args['Raw_label_rename_path']['default'],help='重命名后放在Raw_label_rename文件夹')
    args = parser.parse_args()
    data4_path=args.data4_path
    Raw_label_rename_path=args.Raw_label_rename_path
    if not os.path.exists(Raw_label_rename_path):
        os.makedirs(Raw_label_rename_path)
    file2_list= os.listdir(data4_path)
    name_list=[]#文件名
    test_list = []#后缀名
    for file in file2_list:
        a = file.split('.',1)#将文件名与后缀分开
        name_list.append(a[0])#将文件名单独写入name_list
        test_list.append(a[1])#将文件后缀单独写入test_list
        if a[1]=='json':#判断后缀是否为json(目标)
            with open(data4_path+'/'+file, 'r') as fcc_file:#打开了json文件
                fcc_data = json.load(fcc_file)#读取了json文件内内容，有点像字典
                fcc_data=fcc_data['file_name']#读出json文件中，file_name->hw101.jilin-ai.com_33_D20220714180000_20220715060000_0-0000074310.jpg
                fcc_data=(fcc_data.rsplit('.',1))[0]#将file_name划分为图片名和后缀，取[0],图片名
                if a[0]==fcc_data:#如果当前的文件名和json文件中filename一致，不需要重命名
                    print(file+"原名正确,为"+fcc_data)
                    shutil.copy(os.path.join(data4_path,file),Raw_label_rename_path)#复制到重命名后文件夹中
                else:
                    print(file+"原名不正确,应为"+fcc_data) 
                    os.rename(os.path.join(data4_path,file),os.path.join(Raw_label_rename_path,fcc_data)+".json")#复制到重命名后文件夹中
                    print('已重命名',file,"为",fcc_data)                      
        else:
            continue
    #删除data2_path，新标签文件，防止文件重复
    # os.rmdir(data4_path) # data2_path是文件夹路径

rename_new_label()#将需要重命名标签与名称正确标签存储在一起，存在指定位置
