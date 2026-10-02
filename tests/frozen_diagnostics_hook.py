"""仅测试构建启用：记录启动异常和阻塞启动的提示框，不进入正式发行版。"""
import os
import sys
import traceback

output = os.environ.get('DESKTOP_PET_DIAGNOSTIC_LOG')
if output:
    def record(text):
        with open(output, 'a', encoding='utf-8') as stream:
            stream.write(text + '\n')

    def report_exception(kind, value, tb):
        record(''.join(traceback.format_exception(kind, value, tb)))

    sys.excepthook = report_exception
    from PySide6.QtWidgets import QMessageBox
    for name in ('warning', 'information', 'critical'):
        def message(*args, _name=name, **kwargs):
            record(_name + ': ' + repr(args[1:]))
            return QMessageBox.Ok
        setattr(QMessageBox, name, message)
    record('hook loaded, executable=' + sys.executable + ', bundle=' + str(getattr(sys, '_MEIPASS', '')))
