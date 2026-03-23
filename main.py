import sys
from PySide6.QtWidgets import QApplication
from viewer import MainWindow

def main():
    app = QApplication(sys.argv)
    app.setApplicationName('Multi Image Viewer')
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()