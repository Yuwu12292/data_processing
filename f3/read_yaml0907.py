
def read_yaml(path):
    import yaml
    import os,sys
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath('read_yaml0907.py'))))
    file = open(path, 'r', encoding='utf-8')
    string = file.read()
    args = yaml.safe_load(string)
    return args