#导入模块
from __future__ import annotations
from asyncio import exceptions
from logging import exception
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
import matplotlib.image as pli
import argparse
import traceback
def make_Unusual_imagess():
    from read_yaml import read_yaml
    path = 'editordata.yaml'
    args = read_yaml(path)
    parser = argparse.ArgumentParser(description="检查生成的json文件是否有打不开的图片")
    parser.add_argument('--coco_val_json_name',type=str,default=args['coco_val_json_name']['default'],help='测试集json文件')
    parser.add_argument('--Unusual_imagess',type=str,default=args['Unusual_imagess']['default'],help='有问题的图片字典')
    args = parser.parse_args()
    coco_val_json_name=args.coco_val_json_name
    Unusual_imagess=args.Unusual_imagess



    with open(args.coco_val_json_name, 'r') as fcc_file_new:#打开了json文件
        fcc_data = json.load(fcc_file_new)#读取了json文件内内容，有点像字典

        print(len(fcc_data['images']))
        print(len(fcc_data['annotations']))
        print(fcc_data.keys())
        imagess=fcc_data['images']
        annotations=fcc_data['annotations']
        categories=fcc_data['categories']
        i=0
        Unusual_imagess={}
        imagess=fcc_data['images']
        for num in range(len(imagess)):

            pic=imagess[num]['file_name']
            img=imagess[num]['local_path']
            try:
                im=plt.imread(img) #第0个图片
                im=Image.fromarray(im)   #正常应该是image.open()
                print(im,"-->",i)
                plt.imshow(im)
                plt.show()
                i=i+1
            except:
                print("出错啦")
                print(img,"--->",i)
                print(pic.rsplit('.',1)[0])
                Unusual_imagess[num]=pic.rsplit('.',1)[0]+'.json'
                continue
    print(Unusual_imagess.keys())
    np.save(args.Unusual_imagess,Unusual_imagess)

make_Unusual_imagess()