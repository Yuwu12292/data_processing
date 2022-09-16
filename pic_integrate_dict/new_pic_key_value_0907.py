#导入模块
import os,sys                       
import os
from re import L
import numpy as np
# from shapely.geometry import Polygon
# from PIL import Image
# from tqdm import tqdm
# import json
import pdb
# from pycocotools.coco import COCO
import numpy as np
# import skimage.io as io
# import matplotlib.pyplot as plt
import os
# import shutil
import cv2
import os
# import re
import glob
import time

import argparse

def new_pic_key_value():
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath('read_yaml0907.py'))))
    from f3.read_yaml0907 import read_yaml 
    args = read_yaml('default.yaml')
    parser = argparse.ArgumentParser(description="生成完整的图片字典")

    parser.add_argument('--img_root',type=str,default=args['img_root']['default'],help='所有图片总路径————Image路径')
    parser.add_argument('--pic_key_value_name',type=str,default=args['pic_key_value_name']['default'],help='存储所有图片的字典文件')
    args = parser.parse_args()
    img_root = args.img_root
    pic_key_value_name=args.pic_key_value_name
    image_path=img_root
    pic_key_value={}
    print("frames开始写入")
    T1 = time.time()
    frames=glob.glob(image_path+"/**",recursive=True)
    T2 = time.time()
    print("frames写入结束")
    print('程序运行时间:%s毫秒' % ((T2 - T1)*1000))
    print("总数为",len(frames))

    n=0
    for frame in frames:
        if os.path.isdir(frame):
            print(frame,"------it's a directory")       
        elif os.path.isfile(frame):
            print(frame,"----it's a normal file")
            frame_dir=frame.rsplit('/',1)[0]+"/"
            print("路径是---",frame_dir)
            frame_name=(frame.rsplit('/',1)[1]).rsplit('.',1)[0]
            print("图片名是",)
            n=n+1
            pic_key_value[frame_name+".jpg"]=frame_dir
        else:
            print(frame,"-----it's a special file(socket,FIFO,device file)")
    np.save(pic_key_value_name,pic_key_value)#注意带上后缀名
    print("图片数为：",n)




new_pic_key_value()

