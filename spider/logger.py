import logging
from datetime import datetime
from os import path, makedirs

logging.basicConfig(format='[%(asctime)s] %(levelname)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

check_akshare_dir = "logs"
if not path.exists(check_akshare_dir):
    makedirs(check_akshare_dir)

# 创建一个FileHandler对象，指定日志文件路径和名称、编码格式等参数
fh = logging.FileHandler('logs/{}.log'.format(datetime.now().strftime("%Y%m%d%H%M%S")), encoding='utf-8')
fh.setLevel(logging.DEBUG)
fh.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s %(message)s'))

log.addHandler(fh)
