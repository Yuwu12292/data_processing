import numpy as np
from shapely.geometry import Polygon
def extract_annotation(image_id, category_id, annotation_id, is_crowd, exterior):
    exterior = np.array(exterior)
    segmentation = exterior.ravel().tolist()
    polygon = Polygon(exterior)
    xmin, ymin, xmax, ymax = polygon.bounds
    x = xmin; y = ymin
    width = xmax - xmin; height = ymax - ymin
    bbox = (x, y, width, height)
    area = polygon.area

    annotation = {
            'segmentation': [segmentation],
            'iscrowd': is_crowd,
            'image_id': image_id,
            'category_id': category_id,
            'id': annotation_id,
            'bbox': bbox,
            'area': area}

    return annotation