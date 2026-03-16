import unittest
from main import quicksort, quicksort_inplace


class TestQuicksort(unittest.TestCase):
    """快速排序算法测试用例"""

    def test_empty_array(self):
        """测试空数组"""
        self.assertEqual(quicksort([]), [])

    def test_single_element(self):
        """测试单元素数组"""
        self.assertEqual(quicksort([1]), [1])

    def test_two_elements(self):
        """测试两元素数组"""
        self.assertEqual(quicksort([2, 1]), [1, 2])
        self.assertEqual(quicksort([1, 2]), [1, 2])

    def test_general_case(self):
        """测试一般情况"""
        arr = [3, 1, 4, 1, 5, 9, 2, 6]
        expected = [1, 1, 2, 3, 4, 5, 6, 9]
        self.assertEqual(quicksort(arr), expected)

    def test_sorted_array(self):
        """测试已排序数组"""
        arr = [1, 2, 3, 4, 5]
        expected = [1, 2, 3, 4, 5]
        self.assertEqual(quicksort(arr), expected)

    def test_reverse_sorted_array(self):
        """测试逆序数组"""
        arr = [5, 4, 3, 2, 1]
        expected = [1, 2, 3, 4, 5]
        self.assertEqual(quicksort(arr), expected)

    def test_duplicate_elements(self):
        """测试重复元素"""
        arr = [1, 3, 2, 3, 1, 2, 3]
        expected = [1, 1, 2, 2, 3, 3, 3]
        self.assertEqual(quicksort(arr), expected)

    def test_all_same_elements(self):
        """测试所有元素相同"""
        arr = [5, 5, 5, 5, 5]
        expected = [5, 5, 5, 5, 5]
        self.assertEqual(quicksort(arr), expected)

    def test_large_array(self):
        """测试大数组"""
        import random
        arr = [random.randint(1, 1000) for _ in range(100)]
        sorted_arr = quicksort(arr)
        self.assertEqual(sorted_arr, sorted(arr))

    def test_inplace_sorting(self):
        """测试原地排序"""
        arr = [3, 1, 4, 1, 5, 9, 2, 6]
        original = arr.copy()
        quicksort_inplace(arr)
        expected = sorted(original)
        self.assertEqual(arr, expected)

    def test_inplace_empty_array(self):
        """测试原地排序空数组"""
        arr = []
        quicksort_inplace(arr)
        self.assertEqual(arr, [])

    def test_inplace_single_element(self):
        """测试原地排序单元素数组"""
        arr = [1]
        quicksort_inplace(arr)
        self.assertEqual(arr, [1])

    def test_inplace_sorted_array(self):
        """测试原地排序已排序数组"""
        arr = [1, 2, 3, 4, 5]
        quicksort_inplace(arr)
        self.assertEqual(arr, [1, 2, 3, 4, 5])

    def test_inplace_reverse_sorted_array(self):
        """测试原地排序逆序数组"""
        arr = [5, 4, 3, 2, 1]
        quicksort_inplace(arr)
        self.assertEqual(arr, [1, 2, 3, 4, 5])

    def test_inplace_duplicate_elements(self):
        """测试原地排序重复元素"""
        arr = [1, 3, 2, 3, 1, 2, 3]
        quicksort_inplace(arr)
        expected = [1, 1, 2, 2, 3, 3, 3]
        self.assertEqual(arr, expected)


class TestQuicksortEdgeCases(unittest.TestCase):
    """快速排序算法边界情况测试用例"""

    def test_negative_numbers(self):
        """测试负数"""
        arr = [-3, -1, -4, -1, -5, -9, -2, -6]
        expected = [-9, -6, -5, -4, -3, -2, -1, -1]
        self.assertEqual(quicksort(arr), expected)

    def test_mixed_positive_negative(self):
        """测试正负数混合"""
        arr = [3, -1, 4, -5, 0, 2, -3]
        expected = [-5, -3, -1, 0, 2, 3, 4]
        self.assertEqual(quicksort(arr), expected)

    def test_float_numbers(self):
        """测试浮点数"""
        arr = [3.14, 2.71, 1.41, 0.57, 2.71]
        expected = [0.57, 1.41, 2.71, 2.71, 3.14]
        self.assertEqual(quicksort(arr), expected)

    def test_string_elements(self):
        """测试字符串元素"""
        arr = ["banana", "apple", "cherry", "date"]
        expected = ["apple", "banana", "cherry", "date"]
        self.assertEqual(quicksort(arr), expected)


if __name__ == '__main__':
    unittest.main()