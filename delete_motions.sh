#!/bin/bash
# 删除素晴模型中手往身体前的动作文件

# 需要删除的动作文件列表
DELETE_FILES=(
    "00_Anger_01.motion3.json"
    "00_Anger_02.motion3.json"
    "00_Anger_03.motion3.json"
    "00_Appeal_02.motion3.json"
    "00_Cry_01.motion3.json"
    "00_Cry_02_L.motion3.json"
    "00_Cry_02_R.motion3.json"
    "00_Cry_03.motion3.json"
    "00_Cry_04.motion3.json"
    "00_Doubt_01.motion3.json"
    "00_Doubt_01_cool.motion3.json"
    "00_Excite_01.motion3.json"
    "00_Excite_02.motion3.json"
    "00_Happy_01_Kon.motion3.json"
    "00_Happy_01_pop.motion3.json"
    "00_Happy_02.motion3.json"
    "00_Happy_02_Kon.motion3.json"
    "00_Happy_03.motion3.json"
    "00_Happy_03_Kon.motion3.json"
    "00_Idol_01.motion3.json"
)

echo "开始删除素晴模型的指定动作文件..."
echo "共需要删除 ${#DELETE_FILES[@]} 个动作文件"
echo ""

# 统计
total_deleted=0
total_files=0

# 遍历所有素晴模型文件夹
for model_dir in live2d/model/素晴*/; do
    if [ ! -d "$model_dir" ]; then
        continue
    fi

    model_name=$(basename "$model_dir")
    motions_dir="${model_dir}motions/"

    if [ ! -d "$motions_dir" ]; then
        continue
    fi

    deleted_count=0

    # 删除每个指定的动作文件
    for motion_file in "${DELETE_FILES[@]}"; do
        file_path="${motions_dir}${motion_file}"
        if [ -f "$file_path" ]; then
            rm -f "$file_path"
            if [ $? -eq 0 ]; then
                ((deleted_count++))
                ((total_deleted++))
            fi
        fi
        ((total_files++))
    done

    if [ $deleted_count -gt 0 ]; then
        echo "✓ $model_name: 删除 $deleted_count 个动作文件"
    fi
done

echo ""
echo "================================================================"
echo "删除完成！"
echo "处理了 $(find live2d/model/素晴* -maxdepth 0 -type d 2>/dev/null | wc -l) 个素晴模型"
echo "成功删除 $total_deleted 个动作文件"
echo "================================================================"
