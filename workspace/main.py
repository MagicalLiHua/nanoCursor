def quicksort(arr):
    """
    快速排序算法实现
    
    算法原理:
    1. 选择一个基准元素(pivot)
    2. 将数组分为两部分: 小于基准的元素和大于基准的元素
    3. 递归地对两部分进行快速排序
    
    时间复杂度: 平均 O(n log n), 最坏 O(n^2)
    空间复杂度: O(log n)
    
    参数:
        arr (list): 待排序的列表
    
    返回:
        list: 排序后的列表
    """
    # 基准情况：如果数组长度小于等于1，则已经有序
    if len(arr) <= 1:
        return arr
    
    # 选择中间元素作为基准
    pivot = arr[len(arr) // 2]
    
    # 分割数组
    left = [x for x in arr if x < pivot]      # 小于基准的元素
    middle = [x for x in arr if x == pivot]   # 等于基准的元素
    right = [x for x in arr if x > pivot]     # 大于基准的元素
    
    # 递归排序并合并结果
    return quicksort(left) + middle + quicksort(right)


def quicksort_inplace(arr, low=0, high=None):
    """
    原地快速排序算法实现（更节省空间）
    
    参数:
        arr (list): 待排序的列表
        low (int): 排序范围的起始索引
        high (int): 排序范围的结束索引
    """
    if high is None:
        high = len(arr) - 1
    
    if low < high:
        # 分区操作，返回基准元素的正确位置
        pivot_index = partition(arr, low, high)
        
        # 递归排序基准元素左右两部分
        quicksort_inplace(arr, low, pivot_index - 1)
        quicksort_inplace(arr, pivot_index + 1, high)


def partition(arr, low, high):
    """
    分区函数：将数组分为两部分
    
    参数:
        arr (list): 待分区的数组
        low (int): 分区范围的起始索引
        high (int): 分区范围的结束索引
    
    返回:
        int: 基准元素的最终位置
    """
    # 选择最后一个元素作为基准
    pivot = arr[high]
    
    # 较小元素的索引
    i = low - 1
    
    for j in range(low, high):
        # 如果当前元素小于等于基准
        if arr[j] <= pivot:
            i += 1
            arr[i], arr[j] = arr[j], arr[i]  # 交换元素
    
    # 将基准元素放到正确位置
    arr[i + 1], arr[high] = arr[high], arr[i + 1]
    return i + 1


# 测试函数
def test_quicksort():
    """测试快速排序函数"""
    print("测试快速排序算法...")
    
    # 测试用例
    test_cases = [
        [],                           # 空数组
        [1],                          # 单元素数组
        [3, 1, 4, 1, 5, 9, 2, 6],    # 普通数组
        [1, 2, 3, 4, 5],             # 已排序数组
        [5, 4, 3, 2, 1],             # 逆序数组
        [1, 1, 1, 1],                # 重复元素
        [2, 3, 1, 3, 2, 1, 3, 2]     # 多个重复元素
    ]
    
    for i, test_case in enumerate(test_cases):
        original = test_case.copy()
        sorted_arr = quicksort(test_case)
        print(f"测试 {i+1}: {original} -> {sorted_arr}")
    
    print("\n测试原地快速排序...")
    test_arr = [3, 1, 4, 1, 5, 9, 2, 6]
    print(f"排序前: {test_arr}")
    quicksort_inplace(test_arr)
    print(f"排序后: {test_arr}")


if __name__ == "__main__":
    test_quicksort()