import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath('read_yaml0907.py'))))
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath('dafault.yaml'))))
from f3.read_yaml0907 import read_yaml

args = read_yaml('../dafault.yaml')
print(args['path'])