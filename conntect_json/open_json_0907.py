#导入模块
from __future__ import annotations
import os,sys                       
import os
from re import L
from unicodedata import category
from venv import create
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
def open_json(path,imagess,annotationss):#,categoriess):

    with open(path, 'r') as fcc_file_new:#打开了json文件
        fcc_data = json.load(fcc_file_new)#读取了json文件内内容，有点像字典
        print(len(fcc_data['images']))
        print(len(fcc_data['annotations']))
        print(fcc_data.keys())
        imagess_0=fcc_data['images']
        annotations_0=fcc_data['annotations']
        # categories_0=fcc_data['categories']
        for im in imagess_0:
            imagess.append(im)
        for an in annotations_0:
            annotationss.append(an)
        # for ca in categories_0:
        #     categoriess.append(ca)
    


    

