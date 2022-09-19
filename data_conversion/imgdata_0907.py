import cv2
import numpy as np
import os.path as path
import sys
import os
import argparse
import glob
def imgdata():
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath('read_yaml0907.py'))))
    from f3.read_yaml0907 import read_yaml 
    args = read_yaml('default.yaml')
    parser = argparse.ArgumentParser(description="将提取图片转成.data格式")
    parser.add_argument('--path',type=str,default=args['path']['default'],help='要被整合到一个文件夹的图片存储总路径')
    parser.add_argument('--output_dir',type=str,default=args['output_dir']['default'],help='.data文件存储地址')
    args = parser.parse_args()
    output_dir=args.output_dir
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    files = sorted([os.path.join(args.path, x) for x in os.listdir(args.path)])
    for _, file in enumerate(files):
        img = cv2.imread(file)
        tmp = 0
        camera_id = tmp.to_bytes(4, "little")
        pts = tmp.to_bytes(8, "little")
        (height, width) = img.shape[0:2]

        width = width.to_bytes(2, "little")
        height = height.to_bytes(2, "little")

        output = output_dir + "/" + path.basename(file)

        dot_pos = output.rfind(".")
        output = output[0:dot_pos] + ".data"
        with open(output, "wb") as f:
            f.write(camera_id)
            f.write(width)
            f.write(height)
            f.write(pts)
            f.write(img.tobytes())


imgdata()#将提取图片转成.data格式