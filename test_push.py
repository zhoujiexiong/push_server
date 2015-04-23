'''
Created on Apr 10, 2015

@author: billy
'''
import unittest


def sum( x, y):  
    return x+y  
      
      
def sub( x, y):  
    return x-y 
    
class Test(unittest.TestCase):


    def setUp(self):
        pass


    def tearDown(self):
        pass


    def testSum(self):
        self.assertEqual(sum(1, 1), 2, 'test sum fail')
        
    def test_sub(self):
        self.assertEqual(sub(1, 1), 0, 'test sub fail')

if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()