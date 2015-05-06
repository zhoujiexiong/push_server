# -*- coding:utf-8 -*-

import uuid
import time
import random
import json
import copy
import traceback
import logging
import os

#formatter = logging.Formatter('[%(asctime)s] %(levelname)s %(name)s: %(message)s')
# formatter = logging.Formatter('[%(asctime)s] %(funcName)s: %(message)s')
# handler = logging.StreamHandler()
# handler.setFormatter(formatter)
# logger = logging.getLogger('PUSH_SERVER')
# logger.setLevel(logging.DEBUG)
# logger.addHandler(handler)
# 在 tornado 环境下使用以下两行即可，因为 tornado 对 root logger 做了配置
logger = logging.getLogger()
#logger.setLevel(logging.DEBUG)

from tornado import gen
from tornado.ioloop import IOLoop
from tornado.ioloop import PeriodicCallback
from tornado.tcpserver import TCPServer
from tornado.escape import utf8
from tornado.options import define, options 

# https://github.com/leporo/tornado-redis
import redis
import tornadoredis
# from tornadoredis.client import Client
from tornadoredis.pubsub import BaseSubscriber

# tmp
# import PushServiceMessage_pb2
# import socket
import struct
# from reportlab.pdfbase.pdfdoc import __Comment__
# from _dbus_bindings import Message
import types
from tornado.gen import coroutine
import tornado


def add_callback(callback, *args, **kwargs):
    IOLoop.instance().add_callback(callback, *args, **kwargs)

def add_timeout(delay, callback, *args, **kwargs):
    # return IOLoop.instance().add_timeout(deadline, callback, *args, **kwargs)
    return IOLoop.instance().call_later(delay, callback, *args, **kwargs)

def remove_timeout(timeout):
    IOLoop.instance().remove_timeout(timeout)

MAX_MSG_LEN = 4096
# MAGIC_CODE = 0xfefefefe
CLIENT_TTL = 30

CONNECTION_POOL = tornadoredis.ConnectionPool(max_connections=128,
                                              wait_for_available=True)

# NOTE: 用这个方式能够更好的在跨进程跨主机间传递数据
class PushSubscriber(BaseSubscriber):
    @gen.coroutine
    def on_message(self, msg):
        if not msg:
            return
        #print 'message.kind: %s, body: %s, channel: %s' % (msg.kind, msg.body, msg.channel)
        try:
            if msg.kind == 'message' and msg.body:
                if 'test' == msg.channel:
                    logger.info(str(PushServer.endpoints.keys()))
                    
                if 'forward' == msg.channel:
                    message = json.loads(msg.body)
                    #if PushServer.endpoints.has_key(message['to']):
                    if message['to'] in PushServer.endpoints.keys():
                        PushServer.endpoints[message['to']].on_message(msg.body)
                elif 'notify' == msg.channel:
                    # TODO: 根据设备与客户端的关系筛选出订阅者并对其发送 on_message 消息
                    redis_client = PushServer.redis_client()
                    msg_json = json.loads(msg.body)
                    device_key = 'device:%s' % (msg_json['from'])
                    users = yield gen.Task(redis_client.hget, device_key, 'users')
                    if '' != users:
                        s1 = set(PushServer.endpoints.keys())
                        s2 = set(users.split(':'))
                        # print 's1:', s1, 's2:', s2
                        for user_id in (s1 & s2):
                            # print 'push to uuid:', user_id
                            PushServer.endpoints[user_id].on_message(msg.body)
                    yield gen.Task(redis_client.disconnect)
                elif 'broadcast' == msg.channel:
                    # Get the list of subscribers for this channel
                    subscribers = list(self.subscribers[msg.channel].keys())
                    if subscribers:
                        for subscriber in subscribers:
                            #print msg.body
                            logger.debug(msg.body)
                            subscriber.on_message(msg.body)
        except Exception as e:
            #print 'Push subscriber on_message exception:', e
            logger.debug('subscriber on_message exception: ' + str(e))
        super(PushSubscriber, self).on_message(msg)


class PushClient(object):
    def __init__(self, stream, address):
        self._address = address
        self._stream = stream
        self._stream.set_close_callback(self.on_close)
        # register handler callback
        self._callback = {}
        # peer to server
        self._callback['register'] = self.handle_register
        self._callback['heartbeat'] = self.handle_heartbeat
        # server to peer, peer to peer
        self._callback['ack'] = self.handle_ack
        # server to peer, peer to peer
        self._callback['message'] = self.handle_message  # common message
        # peer to peer
        self._callback['request'] = self.handle_request
        self.update_ttl()
        self._periodic = PeriodicCallback(self.check_ttl, CLIENT_TTL * 1000)
        self._periodic.start()
        self._is_registered = False
        self._endpoint_type = 'unknown'
        self._uuid = None
        self._request_timeouts = {}

    def update_ttl(self):
        self._ttl = time.time() + CLIENT_TTL

    def check_ttl(self):
        # print 'check_ttl'
        if time.time() >= self._ttl:
            if self._uuid is not None:
                logger.debug('check_ttl fail, be about to close stream(%s, %s)' % (self._uuid, str(self._address)))
            else:
                logger.debug('check_ttl fail, be about to close stream(without register: %s)' % str(self._address))
            self.close()

    def close(self):
        self._stream.close()

    @gen.coroutine
    def on_close(self):
        #logger.debug('Push client is about to close')
        self._periodic.stop()
        if not self._is_registered:
            return
        self._is_registered = False
        self.subscribe(False)
        #if self._uuid is not None and PushServer.endpoints.has_key(self._uuid):
        if self._uuid in PushServer.endpoints.keys():
            del PushServer.endpoints[self._uuid]
        for timeout in self._request_timeouts.itervalues():
            remove_timeout(timeout)
            # ERROR: RuntimeError: dictionary changed size during iteration
            #del self._request_timeouts[request_id]
        self._request_timeouts.clear()
        redis_client = PushServer.redis_client()
        endpoint_key = '%s:%s' % (self._endpoint_type, self._uuid)
        try:
            if 'client' == self._endpoint_type:
                devices = yield gen.Task(redis_client.hget, endpoint_key, 'devices')
                if '' != devices:
                    devices = devices.split(':')
                    yield gen.Task(self.unsubscribe_devices, devices)
                yield gen.Task(redis_client.delete, endpoint_key);
            else:
                yield gen.Task(self.update_device_presence, 'offline')
        except Exception as e:
            logger.error('on close exception: ' + str(e))
        yield gen.Task(redis_client.disconnect)        
            
    @gen.coroutine
    def on_message(self, message):
        try:
            #self._request_timeouts[request_id] 有可能在超时处理函数中就被删掉了
            #所以这里有可能会发生异常[KeyError]，ack 也不会往客户端推了
            msg = json.loads(message)
            if 'ack' == msg['type']:
                request_id = msg['request_id']
                remove_timeout(self._request_timeouts[request_id])
                del self._request_timeouts[request_id]
            # NOTE: stream.write 函数不支持 UNICODE 数据
            message = utf8(message)
            message = '%x\r\n%s\r\n' % (len(message), message)
            # push message to endpoint here
            yield self._stream.write(message)
        except Exception as e:
            logger.debug('on message exception, ignored: ' + str(e))
            # self.on_close()
            
    @gen.coroutine
    def run(self):
        try:
            while True:                
                # print 'Push client run: will read'
                length = yield self._stream.read_until('\r\n', max_bytes=MAX_MSG_LEN)
                # print 'Push client run: did read'
                length = int(length, 16)
                if MAX_MSG_LEN < length:
                    logger.debug('message length is too large')
                    break
                message = yield self._stream.read_until('\r\n', max_bytes=MAX_MSG_LEN)
                message = json.loads(message[0:-2])
                message_type = message['type']
                # 连接上后第一条请求必须是 register
                if not self._is_registered and 'register' != message_type:
                    self.send_ack(message_type, False, "must register first")
                    # NOTE: 正式发布时把下行打开
                    #self.close()
                    return
                yield gen.Task(self._callback[message_type], message)
                #self._callback[message_type](message)
        except Exception as e:
        # except:
            #logger.debug('read exception: ' + str(e))
            # print traceback.format_exc()
            self.close()

    def subscribe(self, is_subscribe=True):        
        channels = ('forward', 'notify', 'broadcast', 'keep_alive', 'test')
        subscriber = PushServer.subscriber()
        if is_subscribe:
            subscriber.subscribe(channels, self)
        else:
            # NOTE: unsubscribe 不支持元组或列表
            for channel in channels:
                subscriber.unsubscribe(channel, self)
    
    @gen.coroutine
    def publish(self, channel, message):
        redis_client = PushServer.redis_client()
        yield gen.Task(redis_client.publish, channel, message)
        yield gen.Task(redis_client.disconnect)
        
    @gen.coroutine
    def notify(self, message):
        yield gen.Task(self.publish, 'notify', message)
    
    @gen.coroutine
    def forward(self, message):
        yield gen.Task(self.publish, 'forward', message)
    
    @gen.coroutine
    def broadcast(self, message):
        yield gen.Task(self.publish, 'broadcast', message)

    def pack(self, message):
        message = json.dumps(message)
        message = '%x\r\n%s\r\n' % (len(message), message)
        # print message
        return message
    
    # 由于发生在本连接内，不需要 REDIS 转发，所以 from/to 就不需要了
    @gen.coroutine
    def send_ack(self, sub_type, result, reason=None, **kwargs):
        try:
            ack = {}
            ack['type'] = 'ack'
            ack['sub_type'] = sub_type
            ack['result'] = result
            if reason is not None:
                ack['reason'] = reason
            ack = dict(ack, **kwargs)#合并两 dict
            ack = self.pack(ack)
            yield self._stream.write(ack)
        except Exception as e:
            logger.debug('send ack fail, close stream: ' + str(e))
            # 及时回收连接
            self.close()
           
    @gen.coroutine
    def subscribe_devices(self, devices):
        redis_client = PushServer.redis_client()
        pipe = redis_client.pipeline()
        for device in devices:
            device_key = 'device:%s' % device
            # NOTE: 相当于等待其执行完成，但不让出 CPU
            users = yield gen.Task(redis_client.hget, device_key, 'users')
            if '' == users:
                pipe.hset(device_key, 'users', self._uuid)
            else:
                if self._uuid not in users:
                    users += ':%s' % self._uuid
                    #print 'register users:', users
                    pipe.hset(device_key, 'users', users)
        yield gen.Task(pipe.execute)
        yield gen.Task(redis_client.disconnect)
    
    @gen.coroutine
    def unsubscribe_devices(self, devices):
        redis_client = PushServer.redis_client()
        for device in devices:
            device_key = 'device:%s' % device
            users = yield gen.Task(redis_client.hget, device_key, 'users')
            if '' != users and self._uuid in users:
                users = users.split(':')
                users.remove(self._uuid)
                yield gen.Task(redis_client.hset, device_key, 'users', ':'.join(users))
        yield gen.Task(redis_client.disconnect)
        
    @gen.coroutine
    def get_online_devices(self, devices):
        redis_client = PushServer.redis_client()
        online_devices = []
        for device in devices:
            device_key = 'device:%s' % device
            presence = yield gen.Task(redis_client.hget, device_key, 'presence');
            if '' != presence and 'online' == presence:
                online_devices.append(device)
        yield gen.Task(redis_client.disconnect)
        raise gen.Return(online_devices)

    @gen.coroutine
    def handle_register(self, message):
        try:
            if 'client' != message['endpoint_type'] and 'device' != message['endpoint_type']:
                self.send_ack(message['type'], False, 'unknown endpoint type')
                self.close()
                return
            # 把现有的连接挤下线不合理，返回重复的 uuid
            # endpoint 网络发生切换时会出现这个情况：旧的连接已失效了，但服务端还没反应过来，而这时重连请求
            # 过来了。目前采取的策略是，等待旧的连接超时，这会导致 endpoint 重连成功前会有几次失败
            # 后面验证做好了可以改成将旧连接挤下线以提高体验
            if message['from'] in PushServer.endpoints.keys():
                self.send_ack(message['type'], False, 'duplicate uuid')
                logger.error("duplicate uuid: %s" % message['from'])
                self.close()
                return
            
            # 这里首先要订阅 redis 频道，否则在通知上线后订阅频道前对设备的请求无法响应
            if not self._is_registered:
                self._uuid = message['from']
                self._endpoint_type = message['endpoint_type']
                PushServer.endpoints[self._uuid] = self
                self.subscribe()
                self._is_registered = True
            
            logger.debug('handle register(%s)' % self._uuid)
            
            # UPDATE BI-DIRECTIONAL RELATIONSHIP，支持多次注册，用于添加删除设备时          
            # if device, then notify the relative user currently online
            # if hifocus client, construct the ownership mapping here, otherwise retrieve ownership from DB
            if 'client' == self._endpoint_type:
                redis_client = PushServer.redis_client()
                endpoint_key = '%s:%s' % (self._endpoint_type, self._uuid)
                cur_devices = yield gen.Task(redis_client.hget, endpoint_key, 'devices')
                new_devices = message['devices']
                if '' != cur_devices:
                    # 不为 '' 意味着不是第一次 register
                    # 处理增删设备, new - cur(add), cur - new(del)
                    cur_devices = cur_devices.split(':')
                    new_devices = set(message['devices']) - set(cur_devices)
                    deleted_devices = list(set(cur_devices) - set(message['devices']))
                    yield gen.Task(self.unsubscribe_devices, deleted_devices)
                yield gen.Task(self.subscribe_devices, new_devices)
                yield gen.Task(redis_client.hset, endpoint_key, 'devices', ':'.join(message['devices']))
                yield gen.Task(redis_client.disconnect)
                online_devices = yield gen.Task(self.get_online_devices, message['devices'])
                logger.debug('online devices: ' + str(online_devices))
                self.send_ack(message['type'], True, online_devices=online_devices)
            else:
                yield gen.Task(self.update_device_presence, 'online')
                self.send_ack(message['type'], True)
        except Exception as e:
            logger.error('handle register exception: ' + str(e))
            self.send_ack(message['type'], False, str(e))
            self.close()            
        
    '''
    {
        [required]
        type: message,
        from: <UUID>,
        sub_type: <alarm | device_online | device_offline | ...>,
        [optional]
    }
    '''
    # NOTE: gen.coroutine 会吃掉异常，具体原因可以通过其源码了解
    # message is dict type
    @gen.coroutine
    def handle_message(self, message):
        #print 'handle message'
        try:
            # format check
            message['from']
            message['sub_type']
            # NOTE: 设备的消息都需要 notify, 而来自客户的消息 forward 即可
            message = json.dumps(message)
            if 'client' == self._endpoint_type:
                yield gen.Task(self.forward, message)                
            else:
                yield gen.Task(self.notify, message)
        except Exception as e:
            logger.error('handle message exception: ' + str(e))
            self.send_ack("message", False, str(e))
    
    @gen.coroutine
    def handle_heartbeat(self, message):
        # NOTE: 规避在线但连不上的问题(endpoints 中的对象无端消失导致)
        #if not PushServer.endpoints.has_key(self._uuid):
        if self._uuid not in PushServer.endpoints.keys():
            #PushServer.endpoints[self._uuid] = self
            logger.error('endpoint object(%s) was missing...' % self._uuid);
        self.update_ttl()
        self.send_ack('heartbeat', True)
        if 'client' == self._endpoint_type:
            redis_client = PushServer.redis_client()
            endpoint_key = '%s:%s' % (self._endpoint_type, self._uuid)
            # DEBUG
            yield gen.Task(redis_client.hset, endpoint_key, 'pid', os.getpid())
            yield gen.Task(redis_client.hset, endpoint_key, 'presence_ts', time.asctime())
            yield gen.Task(redis_client.disconnect)
        else:
            # TODO: 心跳包中可带有更丰富的状态信息
            # TODO: 服务端本地要做频度控制
            yield gen.Task(self.update_device_presence, 'online')
              
    @gen.coroutine
    def update_device_presence(self, presence):
        redis_client = PushServer.redis_client()
        endpoint_key = '%s:%s' % (self._endpoint_type, self._uuid)
        cur_presence = yield gen.Task(redis_client.hget, endpoint_key, 'presence')
        # DEBUG
        yield gen.Task(redis_client.hset, endpoint_key, 'pid', os.getpid())
        yield gen.Task(redis_client.hset, endpoint_key, 'presence_ts', time.asctime())
        if '' == cur_presence or presence != cur_presence:
            yield gen.Task(redis_client.hset, endpoint_key, 'presence', presence)
            msg = {}
            msg['type'] = 'message'
            msg['sub_type'] = 'device_%s' % presence
            msg['from'] = self._uuid
            yield gen.Task(self.notify, json.dumps(msg))
        yield gen.Task(redis_client.disconnect)
    
    def handle_request_timeout(self, request_id, sub_type):
        try:
            remove_timeout(self._request_timeouts[request_id])
            del self._request_timeouts[request_id]
        except Exception as e:
            logger.error('handler request timeout exception: ' + str(e))
        self.send_ack(sub_type, False, 'timeout', request_id=request_id)
    
    # peer to peer
    @gen.coroutine
    def handle_request(self, message):
        # NOTE: check if peer online or not[暂时不这么处理，为了减轻 redis 的负担]
        sub_type = 'Unknown'
        try:
            sub_type = message['sub_type']
            request_id = message['request_id']
            message['from']
            message['to']
            timeout = add_timeout(10, self.handle_request_timeout, request_id, sub_type)
            self._request_timeouts[request_id] = timeout
            yield gen.Task(self.forward, json.dumps(message))
        except Exception as e:
            logger.error('handle request exception: ' + str(e))
            self.send_ack(sub_type, False, str(e))
    
    # peer to peer
    @gen.coroutine
    def handle_ack(self, message):
        sub_type = 'Unknown'
        try:
            #print 'handle ack'
            sub_type = message['sub_type']
            message['request_id']
            message['from']
            message['to']
            yield gen.Task(self.forward, json.dumps(message))
        except Exception as e:
            logger.error('handle ack exception: ' + str(e))
            self.send_ack(sub_type, False, str(e))
    

class PushServer(TCPServer):
    endpoints = {}  # id -> client object, after subscribe request
    _subscriber = None
    _redis_client = None
    # NOTE: 这里使用类方法代替构造函数的原因是考虑多进程的情况，
    #       与 redis 建立连接然后再 fork 进程会异常
    @classmethod
    def subscriber(cls):
        if not cls._subscriber:
            cls._subscriber = PushSubscriber(tornadoredis.Client())
        return cls._subscriber
    
    @classmethod
    def redis_client(cls):
#         if not cls._redis_client:
#             cls._redis_client = tornadoredis.Client()
#             cls._redis_client.connect()
#         return cls._redis_client
        return tornadoredis.Client(connection_pool=CONNECTION_POOL)
    
    def handle_stream(self, stream, address):
        client = PushClient(stream, address)
        #add_callback(client.run)
        #yield gen.Task(client.run)
        client.run()


define("port", default=18888, help="Run server on a specific port", type=int)
  
def main():
    options.parse_command_line()
    server = PushServer()
    server.bind(options.port)
    server.start(0)  # Forks multiple sub-processes
    # server.start(1)  # Forks multiple sub-processes
    IOLoop.instance().start()

if '__main__' == __name__:
    main()

