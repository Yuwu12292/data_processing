import sys
import argparse
import os
import json
import numpy as np
from conntect_json.open_json_0907 import open_json
imagess=[]
annotationss=[]
categoriess=[]
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath('read_yaml0907.py'))))
from f3.read_yaml0907 import read_yaml 
args = read_yaml('../default.yaml')
parser = argparse.ArgumentParser(description="任意拼接选中的json,必要时生成val字典")

parser.add_argument('--coco_val_json_name',type=str,default=args['coco_val_json_name']['default'],help='测试集json文件')
parser.add_argument('--path_list',type=str,default=args['path_list']['default'],help='待拼接的json文件的路径')
parser.add_argument('--create_val_key',type=str,default=args['create_val_key']['default'],help='是否创建测试集字典文件:"yes" or "no"')
parser.add_argument('--new_val_key_value_name',type=str,default=args['new_val_key_value_name']['default'],help='存储当前所有测试集json的字典文件')
args = parser.parse_args()

coco_val_json_name = args.coco_val_json_name    
path_list=args.path_list
for i in path_list:
    # print(i)
    open_json(i,imagess,annotationss)#,categoriess)
    print(len(annotationss))
for i in path_list[:]:
    print(i)
    with open(i, 'r') as fcc_file_new:#打开了json文件
        fcc_data = json.load(fcc_file_new)#读取了json文件内内容，有点像字典
        categories_0=fcc_data['categories']
        categoriess=categories_0
        break
# print(categoriess)

    # print(categoriess)
out_path=coco_val_json_name
out_nm={}
with open(out_path, 'w') as f:
    out_nm['images']=imagess
    out_nm['annotations']=annotationss
    out_nm['categories']=categoriess
    json.dump(out_nm, f)
print("out_nm['images']",len(out_nm['images']))
print("out_nm['annotations']",len(out_nm['annotations']))
print("out_nm['categories']",len(out_nm['categories']))
create_val_key=args.create_val_key
val_key_value={}
if create_val_key=='yes':
        # 只有真的要生成val_key时，才用这句话
        for ann_nm in imagess:
            val_key_value[ann_nm['file_name'].rsplit('.',1)[0]]=ann_nm['file_name'].rsplit('.',1)[0]+'.json'
        np.save(args.new_val_key_value_name,val_key_value)#注意带上后缀名
        print('测试集字典已更新')