import os
import numpy as np
from tqdm import tqdm
import json
from PIL import Image
import argparse
import sys
from convert_annotations import convert_annotations
if __name__ == '__main__': 
        parser = argparse.ArgumentParser(description="根据指定数字范围生成训练集和测试集,也可生成单日的coco文件")
        print(os.system('pwd'))
        sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath('read_yaml0907.py'))))
        sys.path.append("/home/stream_filter/CORE/20220907/f3")
       # from f3 import f4123
        from read_yaml0907 import read_yaml
        args = read_yaml('../default.yaml')
        parser.add_argument('--img_root',type=str,default=args['img_root']['default'],help='所有图片总路径————Image路径')
        parser.add_argument('--zip_json_path',type=str,default=args['zip_json_path']['default'],help='存待生成整合json的文件')

        parser.add_argument('--pic_key_value_name',type=str,default=args['pic_key_value_name']['default'],help='存储所有图片的字典文件')

        parser.add_argument('--train_out_nm',type=str,default=args['train_out_nm']['default'],help="存储训练集的文件")
        parser.add_argument('--val_out_nm',type=str,default=args['val_out_nm']['default'],help="存储测试集的文件")
        parser.add_argument('--same_day_nm',type=str,default=args['same_day_nm']['default'],help="存储测试集的文件")


        parser.add_argument('--train_split_num',type=str,default=args['train_split_num']['default'],help='100000之前的json是训练集')
        parser.add_argument('--valid_split_num',type=str,default=args['valid_split_num']['default'],help='100000之后的json是训练集')

        parser.add_argument('--create_val_key',type=str,default=args['create_val_key']['default'],help='是否创建测试集字典文件:"yes" or "no"')

        parser.add_argument('--zip_json_valid_path',type=str,default=args['zip_json_valid_path']['default'],help='存已经是测试集字典里val_key_value内待整合json,这个可以运行后直接删除')
        parser.add_argument('--val_key_value_name',type=str,default=args['val_key_value_name']['default'],help='存储之前所有测试集json的字典文件')

        parser.add_argument('--new_val_key_value_name',type=str,default=args['new_val_key_value_name']['default'],help='存储当前所有测试集json的字典文件')
        
        parser.add_argument('--sub',type=str,default=args['sub']['default'],help='要创建“train和valid”还是“same_day”')
        args = parser.parse_args()
        val_key_value=np.load(args.val_key_value_name,allow_pickle=True).item()#{...,'hw101.jilin-ai.com_33_D20220714180000_20220715060000_0-0000078120': 'hw101.jilin-ai.com_33_D20220714180000_20220715060000_0-0000078120.json'}
        # category info
        cls_codebook = {'人物':1, '病床':2}      
        categories = [
            # {"supercategory": "ground","id": 1,"name": "ground"},
            {"supercategory": "person","id": 1,"name": "person"},
            {"supercategory": "bed","id": 2,"name": "bed"},
        ]
        # ann_root=args.data4_path#就可以生成当天json文件
        # os.rmdir(args.zip_json_valid_path) #args.zip_json_valid_path是个空的文件夹时，这句话就会删除这个空文件夹
        ann_root =args.zip_json_path##划分出已经在valid集里的，将待处理的放进zip_json
        img_root =args.img_root#所有图片存储位置
        pic_key_value=np.load(args.pic_key_value_name,allow_pickle=True).item()#{..., 'D02_20220426110000-0000008010.jpg': '/mnt/RDTeam/01_BDAI/yanglaoyuan_data/Image/20220706/'}
        sub=args.sub
        create_val_key=args.create_val_key
       
        if sub=='train_valid':
            #存traincoco文件
            train_images, train_annotations = convert_annotations(ann_root, img_root, cls_codebook, 'train',create_val_key)
            coco_ann = {
                'images' : train_images, 
                'annotations' : train_annotations, 
                'categories' : categories}
            #保存train的位置
            train_out_nm = args.train_out_nm
            with open(train_out_nm, 'w') as f:
                json.dump(coco_ann, f)
            print('save train annotations at : {:}'.format(train_out_nm))
            #存validcoco文件
            valid_images, valid_annotations = convert_annotations(ann_root, img_root, cls_codebook, 'valid',create_val_key)
            coco_ann = {
                'images' : valid_images, 
                'annotations' : valid_annotations, 
                'categories' : categories}
            #保存valid的位置
            val_out_nm = args.val_out_nm
            with open(val_out_nm, 'w') as f:
                json.dump(coco_ann, f)
            print('save valid annotations at : {:}'.format(val_out_nm))
        if sub == 'same_day':
            #存the_same_day_coco文件
            same_day_images, same_day_annotations = convert_annotations(ann_root, img_root, cls_codebook, sub,create_val_key)
            coco_ann = {
                'images' : same_day_images, 
                'annotations' : same_day_annotations, 
                'categories' : categories}
            #保存the_same_day_coco的位置
            same_day_nm = args.same_day_nm
            with open(same_day_nm, 'w') as f:
                json.dump(coco_ann, f)
            print('save same_day annotations at : {:}'.format(same_day_nm))
