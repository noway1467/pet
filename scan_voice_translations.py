"""扫描Live2D模型的语音文件并生成翻译数据库"""
import json
import os
import glob

def scan_model_voices(models_dir="dist/DesktopPet/live2d/model"):
    """扫描所有模型的语音文件并提取信息"""

    voice_database = {}

    # 遍历所有模型文件夹
    for model_folder in os.listdir(models_dir):
        model_path = os.path.join(models_dir, model_folder)
        if not os.path.isdir(model_path):
            continue

        print(f"\n扫描模型: {model_folder}")
        print("-" * 60)

        # 查找model.json文件
        model_jsons = []
        for pattern in ["*.model3.json", "*.model.json", "model.json"]:
            model_jsons.extend(glob.glob(os.path.join(model_path, pattern)))

        if not model_jsons:
            print("  未找到模型配置文件")
            continue

        model_json = model_jsons[0]
        print(f"  配置: {os.path.basename(model_json)}")

        # 读取模型配置
        try:
            with open(model_json, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception as e:
            print(f"  读取失败: {e}")
            continue

        # 提取动作和语音信息
        motions = config.get('motions', {})
        if not motions:
            # Cubism 3格式
            motions = config.get('FileReferences', {}).get('Motions', {})

        model_voices = []

        for group_name, motion_list in motions.items():
            if not isinstance(motion_list, list):
                continue

            for idx, motion in enumerate(motion_list):
                if not isinstance(motion, dict):
                    continue

                # 查找sound字段
                sound_file = motion.get('sound') or motion.get('Sound')
                if not sound_file:
                    continue

                # 转换为绝对路径
                sound_path = os.path.normpath(os.path.join(os.path.dirname(model_json), sound_file))

                if not os.path.exists(sound_path):
                    print(f"  警告: 语音文件不存在 - {sound_file}")
                    continue

                # 提取动作文件名（用于生成默认翻译）
                motion_file = motion.get('file') or motion.get('File', '')

                voice_info = {
                    'model': model_folder,
                    'group': group_name,
                    'index': idx,
                    'sound_file': sound_file,
                    'sound_path': sound_path,
                    'motion_file': motion_file,
                    'translation': None  # 待填写
                }

                model_voices.append(voice_info)
                print(f"  [{group_name}#{idx}] {sound_file}")

        if model_voices:
            voice_database[model_folder] = model_voices
            print(f"  共找到 {len(model_voices)} 个语音文件")
        else:
            print("  未找到语音文件")

    return voice_database

def generate_default_translations(voice_database):
    """根据动作组名生成默认翻译"""

    # 动作组对应的默认翻译（中文）
    default_translations = {
        # 摸头相关
        'tap_head': ['好舒服呀~', '嘿嘿~', '再摸摸~', '喜欢被摸头~'],
        'taphead': ['好舒服呀~', '嘿嘿~', '再摸摸~', '喜欢被摸头~'],
        'flick_head': ['哎哟~', '疼疼疼~', '别弹了~', '轻一点嘛~'],

        # 戳身体相关
        'tap_body': ['嗯？', '怎么了？', '找我吗？', '在呢~'],
        'tapbody': ['嗯？', '怎么了？', '找我吗？', '在呢~'],
        'tap': ['嗯？', '怎么了呀~', '在听呢~', '叫我吗？'],

        # 摇晃相关
        'shake': ['哎呀~', '别晃啦~', '好晕~', '要倒了~'],
        'drag': ['哎呀~', '要去哪里？', '别拉我~'],

        # 问候相关
        'hello': ['你好~', '嗨~', '很高兴见到你~', '欢迎~'],
        'greeting': ['你好呀~', '早上好~', '见到你真开心~'],

        # 开心相关
        'happy': ['好开心~', '耶~', '真棒~', '太好了~'],
        'joy': ['好开心啊~', '开心~', '嘿嘿~'],
        'smile': ['笑一个~', '开心~', '嘻嘻~'],

        # 惊讶相关
        'surprise': ['哇~', '诶？', '好厉害~', '太惊讶了~'],
        'shock': ['啊！', '吓一跳~', '没想到~'],

        # 生气相关
        'angry': ['哼~', '生气了~', '不开心~', '讨厌~'],
        'mad': ['哼~', '气死我了~', '真是的~'],

        # 伤心相关
        'sad': ['呜呜~', '好难过~', '不开心~', '心情不好~'],
        'cry': ['要哭了~', '呜呜呜~', '难过~'],

        # 害羞相关
        'shy': ['好害羞~', '不好意思~', '嘿嘿~', '有点不好意思~'],
        'embarrassed': ['好尴尬~', '不好意思啦~'],

        # 其他
        'idle': ['...', '嗯~', '呼~'],
        'wait': ['等等我~', '稍等一下~'],
        'bye': ['再见~', '拜拜~', '下次见~'],
    }

    import random

    for model_name, voices in voice_database.items():
        for voice in voices:
            group = voice['group'].lower()

            # 查找匹配的默认翻译
            translation = None
            for key, texts in default_translations.items():
                if key in group:
                    translation = random.choice(texts)
                    break

            # 如果没有匹配，使用通用翻译
            if not translation:
                translation = random.choice(['嗯~', '啊~', '呀~', '嘿~'])

            voice['translation'] = translation

    return voice_database

def save_voice_database(voice_database, output_file="voice_translations.json"):
    """保存语音数据库到JSON文件"""

    # 转换为可序列化的格式
    serializable_db = {}

    for model_name, voices in voice_database.items():
        serializable_db[model_name] = []
        for voice in voices:
            serializable_db[model_name].append({
                'group': voice['group'],
                'index': voice['index'],
                'sound_file': voice['sound_file'],
                'translation': voice['translation']
            })

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(serializable_db, f, ensure_ascii=False, indent=2)

    print(f"\n语音数据库已保存到: {output_file}")

def print_statistics(voice_database):
    """打印统计信息"""

    total_models = len(voice_database)
    total_voices = sum(len(voices) for voices in voice_database.values())

    print("\n" + "=" * 60)
    print("统计信息")
    print("=" * 60)
    print(f"模型总数: {total_models}")
    print(f"语音总数: {total_voices}")
    print()

    # 按模型显示
    print("各模型语音数量:")
    for model_name, voices in sorted(voice_database.items(), key=lambda x: -len(x[1])):
        print(f"  {model_name}: {len(voices)} 个语音")

if __name__ == "__main__":
    print("=" * 60)
    print("Live2D模型语音扫描工具")
    print("=" * 60)

    # 扫描语音
    voice_db = scan_model_voices()

    # 生成默认翻译
    voice_db = generate_default_translations(voice_db)

    # 保存数据库
    save_voice_database(voice_db)

    # 打印统计
    print_statistics(voice_db)

    print("\n完成！")
    print("\n后续步骤:")
    print("1. 检查生成的 voice_translations.json 文件")
    print("2. 手动编辑翻译文本（可选）")
    print("3. 程序将自动加载此文件并在播放语音时显示翻译")
