import json
import pprint
import argparse
import yaml
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath('read_yaml0907.py'))))
from modulefinder import IMPORT_NAME
from check_play_back.a import aa
from check_play_back.create_config_json_path0907 import create_config_json_path
sys.path.append("20220907/read_file/")
from f3.read_yaml0907 import read_yaml
path = '../dafault.yaml'
args = read_yaml(path)
print(args['path'])