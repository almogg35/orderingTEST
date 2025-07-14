import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import random
import string
from io import BytesIO
from PIL import Image, ImageTk

from barcode import get_barcode_class
from barcode.writer import ImageWriter

# --- 核心功能：產生條碼 ---
def generate_barcode_image(data):
    """
    在記憶體中產生條碼圖片物件，用於預覽。
    """
    try:
        # 建立一個記憶體中的二進位串流
        buffer = BytesIO()
        Code128 = get_barcode_class('code128')
        # 將條碼圖片寫入記憶體，而不是檔案
        Code128(data, writer=ImageWriter()).write(buffer)
        buffer.seek(0) # 將指標移回開頭
        # 使用 Pillow 開啟圖片
        pil_image = Image.open(buffer)
        return pil_image
    except Exception:
        return None

def save_barcode_to_file(data, output_path):
    """
    將條碼儲存為實體檔案。
    """
    try:
        Code128 = get_barcode_class('code128')
        Code128(data, writer=ImageWriter()).save(output_path)
        return True
    except Exception as e:
        messagebox.showerror("儲存失敗", f"發生錯誤：\n{e}")
        return False

# --- GUI 應用程式 ---
class BarcodeApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Code 128 條碼產生器")
        self.root.geometry("450x500") # 增加高度以容納預覽區
        self.root.resizable(False, False)

        style = ttk.Style(self.root)
        style.theme_use("clam")

        self.random_mode = tk.BooleanVar()
        self.barcode_preview_image = None # 用來儲存預覽圖片物件，防止被回收

        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- 元件 ---
        ttk.Label(main_frame, text="請輸入或生成條碼內容：").grid(row=0, column=0, columnspan=2, sticky="w")

        self.data_entry = ttk.Entry(main_frame, width=45, font=("Arial", 12))
        self.data_entry.grid(row=1, column=0, columnspan=2, pady=5)
        self.data_entry.focus_set()

        self.random_check = ttk.Checkbutton(main_frame, text="亂數生成13碼數字條碼", variable=self.random_mode, command=self.toggle_random_mode)
        self.random_check.grid(row=2, column=0, sticky="w", pady=5)

        # --- 新增：重新生成按鈕 ---
        self.regenerate_button = ttk.Button(main_frame, text="重新生成", command=self.regenerate_random)
        
        # --- 新增：預覽區 ---
        ttk.Label(main_frame, text="條碼預覽：").grid(row=4, column=0, columnspan=2, sticky="w", pady=(10,0))
        self.preview_label = ttk.Label(main_frame)
        self.preview_label.grid(row=5, column=0, columnspan=2, pady=5)
        
        # 主要的儲存按鈕
        save_button = ttk.Button(main_frame, text="產生並儲存條碼", command=self.on_save_click)
        save_button.grid(row=6, column=0, columnspan=2, pady=10)

    def update_preview(self):
        """更新預覽圖片。"""
        data = self.data_entry.get().strip()
        if not data:
            self.preview_label.config(image='')
            return

        pil_image = generate_barcode_image(data)
        if pil_image:
            # 必須將 PhotoImage 物件存為實例變數，否則會被 Python 的垃圾回收機制清除
            self.barcode_preview_image = ImageTk.PhotoImage(pil_image)
            self.preview_label.config(image=self.barcode_preview_image)

    def regenerate_random(self):
        """產生新的亂數並更新輸入框與預覽。"""
        self.data_entry.config(state="normal")
        self.data_entry.delete(0, tk.END)
        random_number = ''.join(random.choices(string.digits, k=13))
        self.data_entry.insert(0, random_number)
        self.data_entry.config(state="readonly")
        self.update_preview() # 更新預覽

    def toggle_random_mode(self):
        """切換亂數模式。"""
        if self.random_mode.get():
            self.data_entry.config(state="readonly")
            self.regenerate_button.grid(row=2, column=1, sticky="w", padx=5) # 顯示按鈕
            self.regenerate_random() # 第一次勾選時，立即生成
        else:
            self.data_entry.config(state="normal")
            self.data_entry.delete(0, tk.END)
            self.regenerate_button.grid_remove() # 隱藏按鈕
            self.preview_label.config(image='') # 清除預覽

    def on_save_click(self):
        """處理儲存按鈕點擊事件。"""
        data = self.data_entry.get().strip()
        if not data:
            messagebox.showwarning("輸入錯誤", "內容不能為空！")
            return
        
        # 確保預覽與儲存的內容一致
        self.update_preview()

        output_path = filedialog.asksaveasfilename(
            title="請選擇儲存位置",
            initialfile=f"{data}.png",
            defaultextension=".png",
            filetypes=[("PNG 圖片", "*.png"), ("所有檔案", "*.*")]
        )
        if not output_path:
            return

        if save_barcode_to_file(data, output_path):
            messagebox.showinfo("成功", f"條碼已成功儲存至：\n{output_path}")
            if self.random_mode.get():
                self.regenerate_random() # 成功儲存後，自動產生下一組亂數

# --- 主程式執行區 ---
if __name__ == "__main__":
    main_window = tk.Tk()
    app = BarcodeApp(main_window)
    main_window.mainloop()
