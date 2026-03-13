def quick_sort(arr):
    """
    快速排序函数，返回一个新的排序后列表。
    
    参数:
        arr (list): 待排序的列表
    
    返回:
        list: 排序后的新列表
    """
    if len(arr) <= 1:
        return arr
    
    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    
    return quick_sort(left) + middle + quick_sort(right)


def quick_sort_inplace(arr, low=0, high=None):
    """
    原地快速排序函数，直接修改原列表。
    
    参数:
        arr (list): 待排序的列表
        low (int): 排序范围的起始索引
        high (int): 排序范围的结束索引
    """
    if not arr or len(arr) <= 1:
        return
        
    if high is None:
        high = len(arr) - 1
    
    if low < high:
        # 分区操作，返回基准元素的正确位置
        pi = partition(arr, low, high)
        
        # 递归排序基准元素左右两部分
        quick_sort_inplace(arr, low, pi - 1)
        quick_sort_inplace(arr, pi + 1, high)


def partition(arr, low, high):
    """
    分区函数，用于原地排序的快速排序。
    
    参数:
        arr (list): 待排序的列表
        low (int): 排序范围的起始索引
        high (int): 排序范围的结束索引
    
    返回:
        int: 基准元素的正确位置索引
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