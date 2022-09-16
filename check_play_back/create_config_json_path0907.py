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
import shutil
import cv2
import re
import glob
from pathlib import Path

import argparse
import ast
from modulefinder import IMPORT_NAME
import jsonpath
import pprint
def create_config_json_path():
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath('read_yaml0907.py'))))
    from f3.read_yaml0907 import read_yaml 
    args = read_yaml('default.yaml')
    parser = argparse.ArgumentParser(description="创建图片提取用到的stream_filter和config.json和存储位置(config.json里的path)")
    parser.add_argument('--determination1',type=str,default=args['determination1']['default'],help='选出的新视频存放路径')
    parser.add_argument('--path',type=str,default=args['path']['default'],help='要被整合到一个文件夹的图片存储总路径')
    args = parser.parse_args()
    path=args.path
    print(path)
    determination1=args.determination1
    if not os.path.exists(path):
        os.makedirs(path)
    if not os.path.exists(determination1):
        os.makedirs(determination1)
    with open('../20220907/config_0907__.json','r') as f:
        obj = json.load(f)#['preprocessor']['video_writter']['path']
    pprint.pprint(obj)#  pprint.p
    out_nm=args.determination1+'/'+"config.json"
    obj['preprocessor']['video_writter']['path']=args.path
    obj =str(obj)
    obj = obj.replace('\'', '\"')
    obj = obj.replace('False','false')
    obj = obj.replace('True','true')
    obj=obj+"\n"
    with open(out_nm, 'w') as f:
        f.write(obj)
    with open(out_nm,'r') as f:
        out = json.load(f)
    pprint.pprint(out)   
    source='stream_filter'
    deter=args.determination1+'/'+'stream_filter'
    shutil.copyfile(source, deter)

    



