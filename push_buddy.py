#-*- coding:utf-8 -*-

import redis
import time

def main():
    r = redis.Redis()
    while True:
        r.publish('keep_alive', 'keep_alive')
        time.sleep(60)

if __name__ == '__main__':
    main()