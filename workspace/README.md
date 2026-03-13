# 快速排序实现

本项目实现了两种快速排序算法：一种返回新列表的版本，另一种原地排序版本。

## 功能说明

- `quick_sort(arr)`：返回一个新的排序后列表。
- `quick_sort_inplace(arr)`：直接修改原列表，实现原地排序。

## 使用方法

```python
from src.quick_sort import quick_sort, quick_sort_inplace

# 使用返回新列表的版本
sorted_list = quick_sort([3, 1, 4, 1, 5, 9, 2, 6])
print(sorted_list)  # 输出: [1, 1, 2, 3, 4, 5, 6, 9]

# 使用原地排序版本
arr = [3, 1, 4, 1, 5, 9, 2, 6]
quick_sort_inplace(arr)
print(arr)  # 输出: [1, 1, 2, 3, 4, 5, 6, 9]
```

## 运行测试

要运行测试，请在项目根目录下执行：

```bash
python -m pytest tests/
```

或者使用 Python 的 unittest 模块：

```bash
python -m unittest tests.test_quick_sort
```

## 项目结构

```
.
├── src/
│   └── quick_sort.py
├── tests/
│   └── test_quick_sort.py
├── README.md
└── requirements.txt
```

## 算法复杂度

- **时间复杂度**：
  - 平均情况：O(n log n)
  - 最坏情况：O(n²)
  - 最好情况：O(n log n)
- **空间复杂度**：
  - `quick_sort`：O(n)
  - `quick_sort_inplace`：O(log n)

## 依赖

本项目无需额外依赖。

## 测试覆盖

测试用例覆盖了以下情况：
- 空列表
- 单元素列表
- 两元素列表
- 已排序列表
- 逆序列表
- 随机列表
- 重复元素
- 负数
- 正负数混合
- 大列表
- 原地排序功能