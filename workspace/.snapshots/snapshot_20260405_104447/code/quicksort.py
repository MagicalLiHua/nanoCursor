"""
快速排序算法实现
时间复杂度: O(n log n) 平均情况, O(n²) 最坏情况
空间复杂度: O(log n) 递归栈空间
"""

def quicksort(arr):
    """
    快速排序主函数
    
    参数:
        arr: 待排序的列表
        
    返回:
        排序后的列表
    """
    # 如果列表为空或只有一个元素，直接返回
    if len(arr) <= 1:
        return arr
    
    # 选择基准值（这里选择中间元素）
    pivot = arr[len(arr) // 2]
    
    # 分区操作
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    
    # 递归排序左右子数组并合并结果
    return quicksort(left) + middle + quicksort(right)


def quicksort_inplace(arr, low=0, high=None):
    """
    原地快速排序（更节省空间）
    
    参数:
        arr: 待排序的列表
        low: 子数组起始索引
        high: 子数组结束索引
        
    返回:
        原地排序后的列表
    """
    if high is None:
        high = len(arr) - 1
    
    if low < high:
        # 分区操作，返回基准值的正确位置
        pivot_index = partition(arr, low, high)
        
        # 递归排序左右子数组
        quicksort_inplace(arr, low, pivot_index - 1)
        quicksort_inplace(arr, pivot_index + 1, high)
    
    return arr


def partition(arr, low, high):
    """
    分区函数，用于原地快速排序
    
    参数:
        arr: 待排序的列表
        low: 子数组起始索引
        high: 子数组结束索引
        
    返回:
        基准值的正确位置索引
    """
    # 选择最后一个元素作为基准值
    pivot = arr[high]
    
    # i 指向小于基准值的区域的末尾
    i = low - 1
    
    for j in range(low, high):
        # 如果当前元素小于或等于基准值
        if arr[j] <= pivot:
            i += 1
            # 交换 arr[i] 和 arr[j]
            arr[i], arr[j] = arr[j], arr[i]
    
    # 将基准值放到正确的位置
    arr[i + 1], arr[high] = arr[high], arr[i + 1]
    
    return i + 1


def quicksort_random_pivot(arr):
    """
    使用随机基准值的快速排序（避免最坏情况）
    
    参数:
        arr: 待排序的列表
        
    返回:
        排序后的列表
    """
    import random
    
    if len(arr) <= 1:
        return arr
    
    # 随机选择基准值
    pivot_index = random.randint(0, len(arr) - 1)
    pivot = arr[pivot_index]
    
    # 分区操作
    left = [x for i, x in enumerate(arr) if x < pivot or (x == pivot and i != pivot_index)]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    
    # 递归排序左右子数组并合并结果
    return quicksort_random_pivot(left) + middle + quicksort_random_pivot(right)


if __name__ == "__main__":
    # 示例用法
    test_arr = [64, 34, 25, 12, 22, 11, 90]
    print("原始数组:", test_arr)
    print("快速排序结果:", quicksort(test_arr))
    
    # 测试原地排序
    test_arr2 = [64, 34, 25, 12, 22, 11, 90]
    print("\n原地快速排序:")
    print("排序前:", test_arr2)
    quicksort_inplace(test_arr2)
    print("排序后:", test_arr2)
    
    # 测试随机基准值排序
    test_arr3 = [64, 34, 25, 12, 22, 11, 90]
    print("\n随机基准值快速排序结果:", quicksort_random_pivot(test_arr3))