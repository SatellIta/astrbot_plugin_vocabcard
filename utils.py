# -*- coding: utf-8 -*-
import datetime

def get_beijing_time() -> datetime.datetime:
    """获取北京时间（东八区）- 兼容 Docker 容器 UTC 时间"""
    utc_now = datetime.datetime.utcnow()
    beijing_offset = datetime.timedelta(hours=8)
    return utc_now + beijing_offset
