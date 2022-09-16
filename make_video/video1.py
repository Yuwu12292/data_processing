
from tabnanny import filename_only
import cv2
import numpy as np
import glob
import os

# 其它格式的图片也可以
img_array = []
# for filename in glob.glob('/home/1111/D01_20220708113000/*.jpg'):
for filename in glob.glob('../f4/images/2022-09-07_09_25--2022-09-07_10_00--2000/*.jpg'):
# for filename in glob.glob('/home/hw101.jilin-ai.com_34_D20220714020000_20220714040000_0/*.jpg'):
    # print( filename)   
    img = cv2.imread( filename)
    height, width, layers = img.shape
    size = (640,320)
    img_array.append(img)

# avi：视频类型，mp4也可以
# cv2.VideoWriter_fourcc(*'DIVX')：编码格式
# 5：视频帧率
# size:视频中图片大小
# out = cv2.VideoWriter('/home/1111/D01_20220708113000/D01_20220708113000.mp4',
out = cv2.VideoWriter('../f4/videos/2022-09-07_09_25--2022-09-07_10_00--2.mp4',
                      cv2.VideoWriter_fourcc(*'DIVX'),1, (640,320))

for i in range(len(img_array)):
    # print(img_array[i])
    out.write(img_array[i])
out.release()
