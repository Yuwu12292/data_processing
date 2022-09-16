##上传前整合图片到一个文件夹
# coding=utf-8
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
import glob
from pathlib import Path

import argparse




def new_integrate():
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath('read_yaml0907.py'))))
    from f3.read_yaml0907 import read_yaml
    args = read_yaml('default.yaml')
    parser = argparse.ArgumentParser(description="上传前整合图片到一个文件夹")

    parser.add_argument('--determination2',type=str,default=args['determination2']['default'],help='提取出的图片整合后所存放路径')
    parser.add_argument('--path',type=str,default=args['path']['default'],help='要被整合到一个文件夹的图片存储总路径')
    args = parser.parse_args()


    determination = args.determination2
    path=args.path

    if not os.path.exists(determination):
        os.makedirs(determination)
    folders= os.listdir(path)
    for folder in folders:
        dir = path + '/' +  str(folder)
        files = os.listdir(dir)
        for file in files:
            source = dir + '/' + str(file)
            deter = determination +'/'+ str(file)
            print('复制文件--', deter)
            shutil.copyfile(source, deter)

new_integrate()
