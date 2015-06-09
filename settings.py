# -*-coding:utf-8-*-

REDIS_SERVER = 'push.seeucam.net'

MMDB_COUNTRY = 'data/GeoLite2-Country.mmdb'

DEFAULT_AREA = 'CN'
DISPATCH_MAP = {
                'CN': set(['CN']),
                'US': set(['US', 'CA', 'DE', 'UK', 'FR'])
                # default to HK
                }

PUSH_SERVERS_CN = [
                   {'desc': 'push_0001_sz', 'ip': '120.25.220.159', 'port': '18888'},
                   {'desc': 'push_0002_sz', 'ip': '120.25.220.159', 'port': '18888'},
                   ]
PUSH_SERVERS_US = [
                   {'desc': 'push_0001_us', 'ip': '47.88.0.139', 'port': '18888'},
                   {'desc': 'push_0002_us', 'ip': '47.88.0.139', 'port': '18888'},
                   ]

ICE_SERVERS_CN = [
                   {'desc': 'ice_0001_sz', 'ip': '120.25.220.159', 'port': 'default'},
                   {'desc': 'ice_0002_sz', 'ip': '120.25.220.159', 'port': 'default'},
                   ]
ICE_SERVERS_US = [
                   {'desc': 'ice_0001_us', 'ip': '47.88.0.139', 'port': 'default'},
                   {'desc': 'ice_0002_us', 'ip': '47.88.0.139', 'port': 'default'},
                   ]

CLUSTER = {
           'push': {
                    'CN': PUSH_SERVERS_CN,
                    'US': PUSH_SERVERS_US,
                    },
           'ice': {
                   'CN': ICE_SERVERS_CN,
                   'US': ICE_SERVERS_US,
                   },
           }
