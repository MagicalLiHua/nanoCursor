import unittest
from ..src.quick_sort import quick_sort, quick_sort_inplace


class TestQuickSort(unittest.TestCase):
    
    def test_empty_list(self):
        """测试空列表"""
        self.assertEqual(quick_sort([]), [])
    
    def test_single_element(self):
        """测试单元素列表"""
        self.assertEqual(quick_sort([1]), [1])
    
    def test_two_elements(self):
        """测试两元素列表"""
        self.assertEqual(quick_sort([2, 1]), [1, 2])
        self.assertEqual(quick_sort([1, 2]), [1, 2])
    
    def test_sorted_list(self):
        """测试已排序列表"""
        self.assertEqual(quick_sort([1, 2, 3, 4, 5]), [1, 2, 3, 4, 5])
    
    def test_reverse_sorted_list(self):
        """测试逆序列表"""
        self.assertEqual(quick_sort([5, 4, 3, 2, 1]), [1, 2, 3, 4, 5])
    
    def test_random_list(self):
        """测试随机列表"""
        self.assertEqual(quick_sort([3, 1, 4, 1, 5, 9, 2, 6]), [1, 1, 2, 3, 4, 5, 6, 9])
    
    def test_duplicate_elements(self):
        """测试重复元素"""
        self.assertEqual(quick_sort([5, 2, 5, 2, 5]), [2, 2, 5, 5, 5])
    
    def test_negative_numbers(self):
        """测试负数"""
        self.assertEqual(quick_sort([-3, -1, -4, -1, -5]), [-5, -4, -3, -1, -1])
    
    def test_mixed_positive_negative(self):
        """测试正负数混合"""
        self.assertEqual(quick_sort([-2, 3, -1, 4, 0]), [-2, -1, 0, 3, 4])
    
    def test_large_list(self):
        """测试大列表"""
        import random
        large_list = [random.randint(0, 1000) for _ in range(1000)]
        sorted_list = sorted(large_list)
        self.assertEqual(quick_sort(large_list), sorted_list)
    
    def test_inplace_sort(self):
        """测试原地排序"""
        arr = [3, 1, 4, 1, 5, 9, 2, 6]
        quick_sort_inplace(arr)
        self.assertEqual(arr, [1, 1, 2, 3, 4, 5, 6, 9])
    
    def test_inplace_empty_list(self):
        """测试原地排序空列表"""
        arr = []
        quick_sort_inplace(arr)
        self.assertEqual(arr, [])
    
    def test_inplace_single_element(self):
        """测试原地排序单元素列表"""
        arr = [1]
        quick_sort_inplace(arr)
        self.assertEqual(arr, [1])


if __name__ == '__main__':
    unittest.main()