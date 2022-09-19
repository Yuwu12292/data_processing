import os
import numpy as np
from tqdm import tqdm
import json
from PIL import Image
import argparse
import sys
from extract_annotation import extract_annotation
def convert_annotations(ann_root, img_root, cls_codebook,sub,create_val_key):
    parser = argparse.ArgumentParser(description="划分出已经在valid集里的,将待处理成一个大文件的的放进zip_json")
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath('read_yaml0907.py'))))
    from f3.read_yaml0907 import read_yaml
    args = read_yaml('../default.yaml')
    parser.add_argument('--data4_path',type=str,default=args['data4_path']['default'],help='存储下载的新标签')
    parser.add_argument('--pic_key_value_name',type=str,default=args['pic_key_value_name']['default'],help='存储所有图片的字典文件')
    parser.add_argument('--zip_json_path',type=str,default=args['zip_json_path']['default'],help='存待生成整合json的文件')
    parser.add_argument('--zip_json_valid_path',type=str,default=args['zip_json_valid_path']['default'],help='存已经是测试集字典里val_key_value内待整合json,这个可以运行后直接删除')
    parser.add_argument('--val_key_value_name',type=str,default=args['val_key_value_name']['default'],help='存储所有测试集json的字典文件')
    args = parser.parse_args()
    pic_key_value=args.pic_key_value_name
    files = os.listdir(ann_root)
    # files=glob.glob(ann_root+'/**',recursive=True)#循环遍历
    ann_nms = [f for f in files if f.endswith('json')]   
    print("number of anns('images') : {:}".format(len(ann_nms)))
    if sub == 'same_day':
        ann_nms = ann_nms[:]
    if sub == 'train':
        ann_nms = ann_nms[:args.train_split_num]
    if sub == 'valid':
        ann_nms = ann_nms[args.valid_split_num:]
        if create_val_key=='yes':
            # 只有真的要生成val_key时，才用这句话
            val_key_value={}
            for ann_nm in ann_nms:
                ann_nm_01=ann_nm.rsplit('.',1)#把json文件的文件名和后缀分开
                val_key_value[ann_nm_01[0]]=ann_nm#用json的文件名做key来存储json文件
            np.save(args.new_val_key_value_name,val_key_value)#注意带上后缀名
            print('测试集字典已更新')
    annotations = []
    images = []

    image_id = 1
    annotation_id = 1

    for ann_nm in tqdm(ann_nms, total=len(ann_nms)):
        ann_path = os.path.join(ann_root, ann_nm)
        with open(ann_path) as f:
            anns = json.load(f)#r json
        img_nm = anns['file_name']#.rsplit('.',1)[0]#20220812
        img_root=pic_key_value[img_nm]#r img_root        
        img_path = os.path.join(img_root, img_nm)
        width,height = Image.open(img_path).size
        images.append(
            {
                'file_name':img_nm,
                'height':height,
                'width':width,
                'id':image_id,
                'local_path':img_path
            }
        )
        # format anns
        for ann in anns['anns']:
            cls = ann['class']
            exterior = ann['conetent']['exterior']            
            if cls in cls_codebook.keys():
                category_id = cls_codebook[cls]
                annotation = extract_annotation(image_id, category_id, annotation_id, 0, exterior)
                annotations.append(annotation)
                annotation_id += 1
        image_id += 1    
    return images, annotations 