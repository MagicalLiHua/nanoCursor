def binary_search(arr, target):
    """
    对已排序列表执行二分查找
    :param arr: 已排序的列表
    :param target: 要查找的元素
    :return: 目标值在列表中的索引，若不存在则返回-1
    """
    # 处理空列表情况
    if len(arr) == 0:
        return -1
    
    left, right = 0, len(arr) - 1
    
    while left <= right:
        mid = (left + right) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            left = mid + 1
        else:
            right = mid - 1
    
    # 未找到目标值
    return -1

# 测试用例
# assert binary_search([1, 3, 5], 3) == 1
# assert binary_search([], 1) == -1
# assert binary_search([1, 2, 3], 4) == -1