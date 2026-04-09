from datetime import datetime
from pathlib import Path

import exifread
from PIL import Image
from PIL.ExifTags import TAGS

import utils

img = "static/sample_plot/DJI_20250825091926_0002_D.JPG"
path_obj = Path(img)

images = utils.find_images("static/sample_plot/", utils.OdmType.all)

EXIF_TIME_FIELDS = [
    'EXIF DateTimeOriginal',
    'EXIF DateTimeDigitized',
    'Image DateTime',
]


def get_file_time(file):
    """
    if no exif time, get create time
    """
    file_time = None
    with open(file, 'rb') as f:
        tags = exifread.process_file(f, stop_tag='DateTime', details=False)

        for field in EXIF_TIME_FIELDS:
            # get exif time
            file_time = tags.get(field)
            if file_time is not None:
                break

    # if no exif time, get create time
    if file_time is None:
        return datetime.fromtimestamp(Path(file).stat().st_ctime)

    return datetime.strptime(str(file_time).strip(), "%Y:%m:%d %H:%M:%S")


def sort_files_by_time(file_list):
    """
    Sort files by time
    """
    sorts_time = []

    # get time
    for file in file_list:
        file_time = get_file_time(file)
        sorts_time.append((Path(file).stem, file_time))

    # sort by time and filename
    sorts_time.sort(key=lambda x: (x[0], x[1]))

    sort_dict = {}
    for idx, (filename, file_time) in enumerate(sorts_time, start=1):
        sort_dict[filename] = (idx, file_time)

    return sort_dict


sorted_imgs = sort_files_by_time(images)

print(sorted_imgs.get("DJI_20250825093631_0020_D")[0])

# filename = path_obj.stem
# stat_info = path_obj.stat()
#
# modify_time = stat_info.st_mtime
# access_time = stat_info.st_atime
# create_time = stat_info.st_ctime
#
# print("modify_time:", datetime.fromtimestamp(modify_time))
# print("access_time:", datetime.fromtimestamp(access_time))
# print("create_time:", datetime.fromtimestamp(create_time))
#
# with open(img, 'rb') as f:
#     tags = exifread.process_file(f)
#
#     # shoot_time = tags.get('EXIF DateTimeOriginal')
#     # shoot_time = tags.get('EXIF DateTimeDigitized')
#     shoot_time = tags.get('Image DateTime')
#
#     print(shoot_time)
