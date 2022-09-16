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

def add_prefix_subfolders():#划分出已经在valid集里的，将待处理的放进zip_json
 
    parser = argparse.ArgumentParser(description="划分出已经在valid集里的,将待处理成一个大文件的的放进zip_json")
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath('read_yaml0907.py'))))
    from f3.read_yaml0907 import read_yaml
    args = read_yaml('default.yaml')
    parser.add_argument('--data4_path',type=str,default=args['data4_path']['default'],help='存储下载的新标签')
    # parser.add_argument('--pic_key_value_name',type=str,default=args['pic_key_value_name']['default'],help='存储所有图片的字典文件')
    parser.add_argument('--zip_json_path',type=str,default=args['zip_json_path']['default'],help='存待生成整合json的文件')
    parser.add_argument('--zip_json_valid_path',type=str,default=args['zip_json_valid_path']['default'],help='存已经是测试集字典里val_key_value内待整合json,这个可以运行后直接删除')
    parser.add_argument('--val_key_value_name',type=str,default=args['val_key_value_name']['default'],help='存储所有测试集json的字典文件')
    args = parser.parse_args()
    data4_path=args.data4_path
    zip_json_path=args.zip_json_path
    if not os.path.exists(zip_json_path):
        os.makedirs(zip_json_path)
    zip_json_valid_path=args.zip_json_valid_path
    if not os.path.exists(zip_json_valid_path):
        os.makedirs(zip_json_valid_path)
    # pic_key_value=np.load(args.pic_key_value_name,allow_pickle=True).item()#{..., 'D02_20220426110000-0000008010.jpg': '/mnt/RDTeam/01_BDAI/yanglaoyuan_data/Image/20220706/'}
    val_key_value=np.load(args.val_key_value_name,allow_pickle=True).item()#{...,'hw101.jilin-ai.com_33_D20220714180000_20220715060000_0-0000078120': 'hw101.jilin-ai.com_33_D20220714180000_20220715060000_0-0000078120.json'}

    file2_list= os.listdir(data4_path)
    name_list=[]
    test_list = []
    num=0
    for file in file2_list:      
        a = file.rsplit('.',1)#将文件名与后缀分开
        name_list.append(a[0])#将文件名单独写入name_list
        test_list.append(a[1])#将文件后缀单独写入test_list
        if a[1]=='json':#判断后缀是否为json(目标)
            num=num+1
            with open(data4_path+'/'+file, 'r') as fcc_file:#打开了json文件
                fcc_data = json.load(fcc_file)#读取了json文件内内容，有点像字典
                fcc_data=fcc_data['file_name']#读出json文件中，file_name->hw101.jilin-ai.com_33_D20220714180000_20220715060000_0-0000074310.jpg
                fcc_data=(fcc_data.rsplit('.',1))[0]#将file_name划分为图片名和后缀，取[0],图片名
                print(val_key_value)
                if fcc_data in val_key_value:#如果当前的文件名和json文件中filename一致，不需要重命名                   
                    print(num,"原名"+fcc_data+"已存在于valid")
                    shutil.copy(os.path.join(data4_path,file), zip_json_valid_path)#把是valid的都放在zip_json/valid里
                else:
                    shutil.copy(os.path.join(data4_path,file), zip_json_path)#把不是valid的都放在zip_json里，与valid文件夹同级
                    print(fcc_data)
                    print('已复制',fcc_data,"到",zip_json_path)                      
        else:
            continue

add_prefix_subfolders()#划分出已经在valid集里的，将待处理的放进zip_json