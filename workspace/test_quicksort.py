"""
快速排序算法测试文件
包含多种测试用例来验证算法的正确性
"""

import unittest
import random
from quicksort import quicksort, quicksort_inplace, quicksort_random_pivot


class TestQuickSort(unittest.TestCase):
    """测试快速排序算法的测试类"""
    
    def test_empty_list(self):
        """测试空列表"""
        self.assertEqual(quicksort([]), [])
        self.assertEqual(quicksort_inplace([]), [])
        self.assertEqual(quicksort_random_pivot([]), [])
    
    def test_single_element(self):
        """测试单个元素列表"""
        self.assertEqual(quicksort([5]), [5])
        self.assertEqual(quicksort_inplace([5]), [5])
        self.assertEqual(quicksort_random_pivot([5]), [5])
    
    def test_already_sorted(self):
        """测试已排序的列表"""
        arr = [1, 2, 3, 4, 5]
        expected = [1, 2, 3, 4, 5]
        self.assertEqual(quicksort(arr), expected)
        
        # 测试原地排序
        arr_copy = arr.copy()
        self.assertEqual(quicksort_inplace(arr_copy), expected)
        
        self.assertEqual(quicksort_random_pivot(arr), expected)
    
    def test_reverse_sorted(self):
        """测试逆序列表"""
        arr = [5, 4, 3, 2, 1]
        expected = [1, 2, 3, 4, 5]
        self.assertEqual(quicksort(arr), expected)
        
        # 测试原地排序
        arr_copy = arr.copy()
        self.assertEqual(quicksort_inplace(arr_copy), expected)
        
        self.assertEqual(quicksort_random_pivot(arr), expected)
    
    def test_random_list(self):
        """测试随机列表"""
        arr = [64, 34, 25, 12, 22, 11, 90]
        expected = sorted(arr)
        self.assertEqual(quicksort(arr), expected)
        
        # 测试原地排序
        arr_copy = arr.copy()
        self.assertEqual(quicksort_inplace(arr_copy), expected)
        
        self.assertEqual(quicksort_random_pivot(arr), expected)
    
    def test_duplicate_elements(self):
        """测试包含重复元素的列表"""
        arr = [5, 2, 8, 2, 5, 1, 8, 1]
        expected = sorted(arr)
        self.assertEqual(quicksort(arr), expected)
        
        # 测试原地排序
        arr_copy = arr.copy()
        self.assertEqual(quicksort_inplace(arr_copy), expected)
        
        self.assertEqual(quicksort_random_pivot(arr), expected)
    
    def test_negative_numbers(self):
        """测试包含负数的列表"""
        arr = [-5, 2, -8, 0, 5, -1, 8, 1]
        expected = sorted(arr)
        self.assertEqual(quicksort(arr), expected)
        
        # 测试原地排序
        arr_copy = arr.copy()
        self.assertEqual(quicksort_inplace(arr_copy), expected)
        
        self.assertEqual(quicksort_random_pivot(arr), expected)
    
    def test_large_random_list(self):
        """测试大型随机列表"""
        # 生成包含1000个随机整数的列表
        arr = [random.randint(-1000, 1000) for _ in range(1000)]
        expected = sorted(arr)
        
        # 测试标准快速排序
        result = quicksort(arr)
        self.assertEqual(result, expected)
        
        # 测试原地快速排序
        arr_copy = arr.copy()
        quicksort_inplace(arr_copy)
        self.assertEqual(arr_copy, expected)
        
        # 测试随机基准值快速排序
        result_random = quicksort_random_pivot(arr)
        self.assertEqual(result_random, expected)
    
    def test_all_same_elements(self):
        """测试所有元素都相同的列表"""
        arr = [7, 7, 7, 7, 7]
        expected = [7, 7, 7, 7, 7]
        self.assertEqual(quicksort(arr), expected)
        
        # 测试原地排序
        arr_copy = arr.copy()
        self.assertEqual(quicksort_inplace(arr_copy), expected)
        
        self.assertEqual(quicksort_random_pivot(arr), expected)
    
    def test_floating_point_numbers(self):
        """测试浮点数列表"""
        arr = [3.14, 2.71, 1.41, 0.0, -1.0, 2.0]
        expected = sorted(arr)
        self.assertEqual(quicksort(arr), expected)
        
        # 测试原地排序
        arr_copy = arr.copy()
        self.assertEqual(quicksort_inplace(arr_copy), expected)
        
        self.assertEqual(quicksort_random_pivot(arr), expected)
    
    def test_mixed_types_error(self):
        """测试混合类型列表（应该抛出异常）"""
        arr = [1, "2", 3.0]
        
        # 标准快速排序应该能处理（但结果可能不符合预期）
        # 这里我们只测试原地排序是否会出错
        with self.assertRaises(TypeError):
            # 原地排序在比较时会抛出TypeError
            quicksort_inplace(arr.copy())
    
    def test_partition_function(self):
        """测试分区函数（通过原地排序间接测试）"""
        from quicksort import partition
        
        arr = [10, 80, 30, 90, 40, 50, 70]
        pivot_index = partition(arr, 0, len(arr) - 1)
        
        # 验证分区结果：基准值左边的元素都小于等于基准值，右边的都大于基准值
        pivot_value = arr[pivot_index]
        for i in range(pivot_index):
            self.assertLessEqual(arr[i], pivot_value)
        for i in range(pivot_index + 1, len(arr)):
            self.assertGreater(arr[i], pivot_value)


def run_performance_test():
    """运行性能测试（可选）"""
    print("\n性能测试:")
    
    # 测试不同大小的列表
    sizes = [100, 1000, 10000]
    
    for size in sizes:
        print(f"\n测试列表大小: {size}")
        arr = [random.randint(-10000, 10000) for _ in range(size)]
        
        # 标准快速排序
        import time
        start = time.time()
        quicksort(arr.copy())
        standard_time = time.time() - start
        
        # 原地快速排序
        start = time.time()
        quicksort_inplace(arr.copy())
        inplace_time = time.time() - start
        
        # 随机基准值快速排序
        start = time.time()
        quicksort_random_pivot(arr.copy())
        random_time = time.time() - start
        
        print(f"标准快速排序: {standard_time:.6f} 秒")
        print(f"原地快速排序: {inplace_time:.6f} 秒")
        print(f"随机基准值快速排序: {random_time:.6f} 秒")


if __name__ == "__main__":
    # 运行单元测试
    print("运行快速排序单元测试...")
    unittest.main(exit=False)
    
    # 运行性能测试（可选）
    print("\n" + "="*50)
    run_performance_test()
    
    print("\n所有测试完成！")