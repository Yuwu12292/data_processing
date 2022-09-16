#导入模块
import os,sys                       
import os
from re import L
import numpy as np
# from shapely.geometry import Polygon
# from PIL import Image
# from tqdm import tqdm
import json
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

def new_val_key_value():
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath('read_yaml0907.py'))))
    from f3.read_yaml0907 import read_yaml 
    args = read_yaml('default.yaml')
    parser = argparse.ArgumentParser(description="生成验证集字典")
    parser.add_argument('--data4_path',type=str,default=args['data4_path']['default'],help='存待生成整合json的文件')
    parser.add_argument('--val_key_value_name',type=str,default=args['val_key_value_name']['default'],help='存储所有测试集的字典文件')
    args = parser.parse_args()
    data4_path = args.data4_path

    val_key_value={}#args.val_key_value_name
    print("frames开始写入")
    T1 = time.time()
    frames=glob.glob(data4_path+"/*",recursive=True)
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
            print("json名是",)
            n=n+1
            val_key_value[frame_name]=frame_dir+".json"
        else:
            print(frame,"-----it's a special file(socket,FIFO,device file)")
    np.save(args.val_key_value_name,val_key_value)#注意带上后缀名
    print("json数为:",n)





new_val_key_value()

