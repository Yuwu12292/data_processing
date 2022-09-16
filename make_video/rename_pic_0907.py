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
data4_path='../f4/images/2022-09-07_09_25--2022-09-07_10_00--2000'
file2_list= os.listdir(data4_path)
name_list=[]#文件名
test_list = []#后缀名
for file in file2_list:
    Olddir=os.path.join(data4_path,file)
    # print(Olddir)
    if os.path.isdir(Olddir):
        continue
    filename=os.path.splitext(file)[0]
    filename=filename[40:]
    filetype = os.path.splitext(file)[1]  # 文件后缀名 例如.jpg
    Newdir=os.path.join(data4_path,"2022-09-07_09_25--2022-09-07_10_00--2-"+filename.zfill(11).rsplit(')',1)[0]+filetype)#6位整数
    os.rename(Olddir,Newdir)



