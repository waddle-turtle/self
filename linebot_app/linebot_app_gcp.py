import json
import os
import sys
from datetime import datetime, timedelta, timezone
import google.generativeai as genai

import pygsheets
from flask import Flask, request
from linebot import LineBotApi, WebhookHandler
from linebot.models import TextSendMessage

from linebot_app.config import Config

app = Flask(__name__)
# 修改你的 route，同時允許 GET 和 POST
@app.route("/", methods=["GET", "POST"])
def callback():
    if request.method == 'GET':
        return "Line Bot is running!", 200  # 讓 LINE Verify 可以抓到 200 OK
    return linebot(request)

def linebot(request):
    """Responds to any HTTP request.
    Args:
        request (flask.Request): HTTP request object.
    Returns:
        The response text or any set of values that can be turned into a
        Response object using
        `make_response <http://flask.pocoo.org/docs/1.0/api/#flask.Flask.make_response>`.
    """
    
    try:
        config = Config()
        line_bot_api = LineBotApi(config.LINE_CHANNEL_ACCESS_TOKEN)
        handler = WebhookHandler(config.LINE_CHANNEL_SECRET)

        # get X-Line-Signature header value
        signature = request.headers["X-Line-Signature"]
        # get request body as text
        body = request.get_data(as_text=True)
        body_json = json.loads(body)
        
        # 驗證並處理 webhook
        # 驗證並處理 webhook
        handler.handle(body, signature)
        
        # ====== 加上這段：遇到 LINE Verify 空封包直接過關 ======
        if not body_json.get("events"):
            return "OK"
        # ========================================================

        # 提取訊息和回覆 token
        msg = body_json["events"][0]["message"]["text"]
        tk = body_json["events"][0]["replyToken"]
        print(msg, tk)

        if msg != "":
            try:
                gc = pygsheets.authorize(service_file=config.GDRIVE_JSON)
                wks = gc.open(config.GSPREADSHEET).worksheet_by_title(config.GWORKSHEET)
            except Exception as ex:
                print("無法連線google sheet", ex)
                sys.exit(1)

            bo = BotOperation(wks, line_bot_api, msg, tk, config)
            try:
                op = msg.lstrip().split(" ", 1)[0]
                bo.execute_command(op)
            except KeyError:
                line_bot_api.reply_message(tk, TextSendMessage(text="不支援的指令"))
            except Exception as ex:
                print(ex)
                line_bot_api.reply_message(tk, TextSendMessage(text="指令執行失敗"))
    except Exception as ex:
        print(request.args)
        print(ex)
        sys.exit(1)

    return "OK"


class BotOperation:
    # 表單欄位索引常量
    COL_TIME = 0
    COL_NAME = 1
    COL_ITEM = 2
    COL_TYPE = 3
    COL_AMOUNT = 4
    
    def __init__(self, wks, line_bot_api, msg, tk, config):
        self.wks = wks
        self.api = line_bot_api
        self.msg = msg
        self.tk = tk
        self.config = config

    def _get_all_values(self):
        """獲取所有非空值"""
        return self.wks.get_all_values(
            include_tailing_empty_rows=False, 
            include_tailing_empty=False
        )

    def read(self):
        all_values = self._get_all_values()
        sub_content = [
            f"{i}  {row[self.COL_TIME]:<3s}  {row[self.COL_NAME]:<3s}  "
            f"{row[self.COL_ITEM]:<3s}  {row[self.COL_TYPE]:<3s}  "
            f"{row[self.COL_AMOUNT]:<3s}"
            for i, row in enumerate(all_values)
        ]
        content = "\n".join(sub_content)
        self.api.reply_message(self.tk, TextSendMessage(text=content))

    def display(self, url):
        self.api.reply_message(self.tk, [TextSendMessage(text="完整表單"), TextSendMessage(text=url)])

    def write(self):
        msg_list = self.msg.split(" ")
        if len(msg_list) < 5:
            self.api.reply_message(
                self.tk, 
                TextSendMessage(text="記錄失敗,格式:\nwrite 名字 品項 分類 金額(記得空格)")
            )
            return
        
        name = msg_list[1]
        text = " ".join(msg_list[2:])

        dt_local = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))
        timestamp = dt_local.strftime("%Y-%m-%d %H:%M:%S")
        
        content = []
        success_log = ["記錄成功"]
        total_this_time = 0
        
        for val in text.split("/"):
            val = val.strip()
            if not val:
                continue
                
            parts = val.split(" ")
            if len(parts) != 3:
                error_msg = (
                    f"記錄失敗,格式:\n"
                    f"write 名字 品項1 分類1 金額1/品項2 分類2 金額2(記得空格)\n"
                    f"多筆之間用 / 隔開，write 跟名字只要寫一次\n"
                    f"錯誤位置:\n {val}"
                )
                self.api.reply_message(self.tk, TextSendMessage(text=error_msg))
                return
            
            try:
                amount = float(parts[2])
                per_line = [timestamp, name] + parts
                total_this_time += amount
                content.append(per_line)
                success_log.append(" ".join(per_line))
            except ValueError:
                error_msg = f"金額格式錯誤: {parts[2]}"
                self.api.reply_message(self.tk, TextSendMessage(text=error_msg))

                return
        

        
        # 寫入資料 (繞過 append_table 的 Bug)
        all_rows = self._get_all_values()
        next_row_index = len(all_rows) + 1
        self.wks.update_values(f"A{next_row_index}", content)
        
        # 更新總和
        current_total = float(self.wks.cell("G1").value or 0)
        new_total = current_total + total_this_time
        self.wks.update_value("G1", new_total)
        
        # 回覆訊息
        success_text = "\n".join(success_log)
        messages = [TextSendMessage(text=success_text)]
        
        if new_total >= self.config.THRESHOLD_AMOUNT:
            messages.append(
                TextSendMessage(text=f"目前已消費 {new_total} 元已超過預期")
            )
        
        self.api.reply_message(self.tk, messages)

    def ssum(self, target, kind="sum"):
        all_values = self._get_all_values()
        
        if kind == "sum":
            idx = self.COL_NAME
        elif kind == "type":
            idx = self.COL_TYPE
        else:
            self.api.reply_message(
                self.tk, 
                TextSendMessage(text="ssum function error, 通知皮兒!")
            )
            return
        
        total = 0
        for row in all_values[1:]:  # 跳過標題列
            if row and len(row) > idx and row[idx] == target:
                try:
                    total += float(row[self.COL_AMOUNT])
                except (ValueError, IndexError):
                    print(f"金額格式錯誤: {row[self.COL_AMOUNT]}")
                    return
        
        content = f"{target} 已花費 {total} 元"
        self.api.reply_message(self.tk, TextSendMessage(text=content))

    def get_type(self):
        all_values = self._get_all_values()
        types = set()
        
        for row in all_values[1:]:  # 跳過標題列
            if row and len(row) > self.COL_TYPE:
                types.add(row[self.COL_TYPE])
        
        types_list = sorted(list(types))
        content = f"共有以下 {len(types_list)} 種分類：\n{types_list}"
        self.api.reply_message(self.tk, TextSendMessage(text=content))

    def delete(self, index=None):
        all_values = self._get_all_values()
        
        if len(all_values) <= 1:
            content = "表單為空"
            self.api.reply_message(self.tk, TextSendMessage(text=content))
            return
        
        # 如果沒有指定 index，刪除最後一筆
        if index is None:
            row_num = len(all_values)
            deleted_row = " ".join(all_values[-1])
            self.wks.delete_rows(row_num)
            
            # 更新總和
            current_total = float(self.wks.cell("G1").value or 0)
            new_total = current_total - float(all_values[-1][self.COL_AMOUNT])
            self.wks.update_value("G1", new_total)
            
            content = f"已刪除最後一筆\n{deleted_row}"
        else:
            try:
                # index 對應 all_values 的索引
                # 用戶輸入的 index 就是 read 顯示的索引
                idx = int(index)
                
                if idx < 0 or idx >= len(all_values):
                    content = f"索引錯誤，請輸入 0 到 {len(all_values)-1} 之間的數字"
                    self.api.reply_message(self.tk, TextSendMessage(text=content))
                    return
                
                # 不允許刪除標題列（index 0）
                if idx == 0:
                    content = "無法刪除標題列"
                    self.api.reply_message(self.tk, TextSendMessage(text=content))
                    return
                
                deleted_row = " ".join(all_values[idx])
                # pygsheets 使用 1-based row number
                self.wks.delete_rows(idx + 1)
                
                # 更新總和（idx >= 1 是數據行）
                if idx >= 1 and len(all_values[idx]) > self.COL_AMOUNT:
                    try:
                        amount = float(all_values[idx][self.COL_AMOUNT])
                        current_total = float(self.wks.cell("G1").value or 0)
                        new_total = current_total - amount
                        self.wks.update_value("G1", new_total)
                    except (ValueError, IndexError):
                        pass
                
                content = f"已刪除第 #{idx} 筆\n{deleted_row}"
            except ValueError:
                content = "索引必須是數字"
        
        self.api.reply_message(self.tk, TextSendMessage(text=content))

    def update(self, index, data_str):
        """更新指定索引的資料行"""
        all_values = self._get_all_values()
        
        if len(all_values) <= 1:
            content = "表單為空，無法更新"
            self.api.reply_message(self.tk, TextSendMessage(text=content))
            return
        
        try:
            idx = int(index)
            
            if idx < 0 or idx >= len(all_values):
                content = f"索引錯誤，請輸入 0 到 {len(all_values)-1} 之間的數字"
                self.api.reply_message(self.tk, TextSendMessage(text=content))
                return
            
            # 不允許更新標題列（index 0）
            if idx == 0:
                content = "無法更新標題列"
                self.api.reply_message(self.tk, TextSendMessage(text=content))
                return
            
            # 解析新數據
            parts = data_str.split()
            if len(parts) < 4:
                content = "數據格式錯誤\n格式：時間 名字 品項 分類 金額\n例如：2024-01-01 12:00:00 小美 午餐 餐飲 100"
                self.api.reply_message(self.tk, TextSendMessage(text=content))
                return
            
            # 時間 + 人名 + 品項 + 分類 + 金額
            # 時間可能包含空格，需要特別處理
            if len(parts) == 6:
                # 標準格式：日期 時間 人名 品項 分類 金額
                new_time = f"{parts[0]} {parts[1]}"
                new_name = parts[2]
                new_item = parts[3]
                new_type = parts[4]
                new_amount = parts[5]
            elif len(parts) == 5:
                # 簡化格式：時間 人名 品項 分類 金額
                new_time = parts[0]
                new_name = parts[1]
                new_item = parts[2]
                new_type = parts[3]
                new_amount = parts[4]
            else:
                content = "數據格式錯誤\n格式：時間 名字 品項 分類 金額"
                self.api.reply_message(self.tk, TextSendMessage(text=content))
                return
            
            # 驗證金額
            try:
                float(new_amount)
            except ValueError:
                content = f"金額格式錯誤: {new_amount}"
                self.api.reply_message(self.tk, TextSendMessage(text=content))
                return
            
            # 獲取舊數據的金額（用於更新總和）
            old_amount = 0
            try:
                if len(all_values[idx]) > self.COL_AMOUNT:
                    old_amount = float(all_values[idx][self.COL_AMOUNT])
            except (ValueError, IndexError):
                old_amount = 0
            
            # 構建新數據
            new_row = [new_time, new_name, new_item, new_type, new_amount]
            row_number = idx + 1  # pygsheets 使用 1-based
            
            # 更新該行的數據
            self.wks.update_values(f"A{row_number}", [new_row])
            
            # 更新總和
            new_amount_float = float(new_amount)
            if old_amount != 0 or new_amount_float != 0:
                try:
                    current_total = float(self.wks.cell("G1").value or 0)
                    # 減去舊金額，加上新金額
                    new_total = current_total - old_amount + new_amount_float
                    self.wks.update_value("G1", new_total)
                except (ValueError, IndexError):
                    pass
            
            content = f"已更新第 #{idx} 筆\n{' '.join(new_row)}"
            self.api.reply_message(self.tk, TextSendMessage(text=content))
            
        except ValueError:
            content = "索引必須是數字"
            self.api.reply_message(self.tk, TextSendMessage(text=content))

    def clear(self):
        # 獲取當前試算表的所有工作表
        spreadsheet = self.wks.spreadsheet
        
        # 獲取所有數據用於備份
        all_values = self._get_all_values()
        
        # 嘗試獲取或創建備份工作表
        backup_sheet_name = f"{self.config.GWORKSHEET}_backup"
        try:
            backup_wks = spreadsheet.worksheet_by_title(backup_sheet_name)
        except pygsheets.WorksheetNotFound:
            # 如果備份工作表不存在，創建它
            backup_wks = spreadsheet.add_worksheet(backup_sheet_name, rows=len(all_values) if all_values else 100, cols=7)
        
        # 備份數據到備份工作表
        if all_values:
            backup_wks.clear()
            backup_wks.update_values("A1", all_values)
        
        # 清除原工作表
        self.wks.clear()
        header = ["時間", "人名", "品項", "分類", "費用", "總和", 0]
        self.wks.update_values("A1", [header])
        
        # 記錄備份時間
        dt_local = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))
        timestamp = dt_local.strftime("%Y-%m-%d %H:%M:%S")
        backup_info = f"備份工作表：{backup_sheet_name}\n備份時間：{timestamp}"
        
        self.api.reply_message(self.tk, TextSendMessage(text=f"全部清除成功\n{backup_info}"))

    def revert(self):
        """還原備份的數據"""
        try:
            # 獲取當前試算表
            spreadsheet = self.wks.spreadsheet
            backup_sheet_name = f"{self.config.GWORKSHEET}_backup"
            
            # 嘗試獲取備份工作表
            backup_wks = spreadsheet.worksheet_by_title(backup_sheet_name)
            backup_values = backup_wks.get_all_values(
                include_tailing_empty_rows=False,
                include_tailing_empty=False
            )
            
            if not backup_values or len(backup_values) == 0:
                self.api.reply_message(self.tk, TextSendMessage(text="沒有備份資料可還原"))
                return
            
            # 將備份數據還原到當前工作表
            self.wks.clear()
            self.wks.update_values("A1", backup_values)
            
            self.api.reply_message(self.tk, TextSendMessage(text="備份資料還原成功"))
        except pygsheets.WorksheetNotFound:
            self.api.reply_message(self.tk, TextSendMessage(text="找不到備份工作表"))
        except Exception as ex:
            print(f"還原錯誤: {ex}")
            self.api.reply_message(self.tk, TextSendMessage(text=f"還原失敗: {ex}"))

    def method(self):
        content = (
            "'AI輔助記帳支援中'\n"
            "不需要死背格式！日常對話即可自動判別：\n"
            "read: 讀取資料（顯示索引）\n"
            "display: 完整表單\n"
            "write 名字 品項 分類 金額(記得空格): 記帳\n"
            "write 名字 品項1 分類1 金額1/品項2 分類2 金額2\n"
            "(記得空格，多筆以此類推)\n"
            "sum 名字(記得空格): 加總\n"
            "delete: 刪除最後一筆記錄\n"
            "delete 索引: 刪除指定索引的記錄\n"
            "update 索引 時間 名字 品項 分類 金額: 更新指定記錄\n"
            "例如：update 2 2024-01-01 12:00 小美 午餐 餐飲 100\n"
            "clear: 清除全部項目（會自動備份）\n"
            "revert: 還原備份的資料\n"
            "type: 獲得分類項目\n"
            "type 分類(記得空格): 獲得分類金額加總"
        )
        self.api.reply_message(self.tk, TextSendMessage(text=content))

    def execute_command(self, op):
        """執行對應的指令"""
        match op:
            case "read":
                self.read()
                print("讀取資料成功")
            case "display":
                self.display(self.config.GOOGLE_SHEET_URL)
                print("輸出完整表單")
            case "write":
                self.write()
                print("新增資料到試算表")
            case "sum":
                lst = self.msg.split(' ')
                if len(lst) != 2:
                    self.api.reply_message(self.tk, TextSendMessage(text="查詢失敗,格式:\nsum 名字(記得空格)"))
                else:
                    self.ssum(lst[-1], "sum")
                print("計算總和")
            case "type":
                msg = self.msg.strip()
                lst = msg.split(' ')
                if len(lst) == 1:
                    self.get_type()
                    print("提供分類項目")
                elif len(lst) != 2:
                    self.api.reply_message(self.tk, TextSendMessage(text="查詢失敗,格式:\ntype 或是 type 種類(記得空格)"))
                else:
                    self.ssum(lst[-1], "type")
                    print("計算分類總和")
            case "delete":
                lst = self.msg.split(' ')
                if len(lst) == 1:
                    self.delete()  # 沒有參數，刪除最後一筆
                elif len(lst) == 2:
                    try:
                        index = int(lst[1])
                        self.delete(index)
                    except ValueError:
                        self.api.reply_message(self.tk, TextSendMessage(text="索引必須是數字\n格式：delete 或 delete 索引"))
                else:
                    self.api.reply_message(self.tk, TextSendMessage(text="格式錯誤\n格式：delete 或 delete 索引"))
                print("清除項目")
            case "clear":
                self.clear()
                print("清除資料")
            case "revert":
                self.revert()
                print("還原備份資料")
            case "update":
                lst = self.msg.split(' ', 2)
                if len(lst) < 3:
                    self.api.reply_message(self.tk, TextSendMessage(text="格式錯誤\n格式：update 索引 時間 名字 品項 分類 金額\n例如：update 2 2024-01-01 12:00 小美 午餐 餐飲 100"))
                else:
                    try:
                        idx = int(lst[1])
                        data_str = lst[2]
                        self.update(idx, data_str)
                    except ValueError:
                        self.api.reply_message(self.tk, TextSendMessage(text="索引必須是數字"))
                print("更新資料")
            case "指令":
                self.method()
                print("查詢指令")
            case _:
                # ===== AI 大腦接管區 =====
                try:
                    # 1. 喚醒 Gemini API
                    genai.configure(api_key=self.config.GEMINI_API_KEY)
                    # 使用最新的 flash 模型，反應最快
                    model = genai.GenerativeModel('gemini-2.5-flash') 
                    
                    # 2. 設計 System Prompt (提示詞工程)
                    # 2. 設計升級版 System Prompt (多筆連鎖辨識)
                    prompt = f"""
                    你現在是一個超級聰明的多筆記帳助理。你的任務是從使用者的日常對話中，精準擷取出「所有」記帳資訊。
                    不管使用者講了幾筆花費，你都必須「嚴格」將它們轉換成以下單一行的多筆指令格式（多筆之間請用 / 隔開，write 跟名字只要在最開頭寫一次）：
                    write 名字 品項1 分類1 金額1/品項2 分類2 金額2/品項3 分類3 金額3...

                    【轉換規則】：
                    1. 分類只能從這裡挑選最適合的：飲食、交通、娛樂、購物、居住、醫療、其他。
                    2. 如果使用者沒有說名字（例如只說「買了」、「吃了」），名字一律填寫為「我」。
                    3. 金額前面如果使用者有打品項，記得把品項跟金額拆開。
                    4. 請只輸出轉換後的單行指令結果，絕對不要包含任何解釋、標點符號或廢話。
                    5. 如果使用者的話完全跟記帳無關（例如聊天、打招呼），請用繁體中文親切回應他。

                    【學習範例】：
                    使用者：我今天吃了200塊的便當
                    回答：write 我 便當 飲食 200

                    使用者：200的便當80的紅茶 40針線 150的手機
                    回答：write 我 便當 飲食 200/紅茶 飲食 80/針線 購物 40/手機 購物 150

                    使用者：小美買了100甜甜圈和50奶茶
                    回答：write 小美 甜甜圈 飲食 100/奶茶 飲食 50

                    現在，請處理這句使用者的最新對話：「{self.msg}」
                    """
                    
                    # 3. 把組好的 prompt 丟給 AI 思考
                    response = model.generate_content(prompt)
                    ai_result = response.text.strip()
                    
                    print(f"[AI 處理中] 原始輸入: {self.msg}  --->  轉換結果: {ai_result}")
                    
                    # 4. 判斷 AI 的結果：是指令就執行，是閒聊就直接回覆
                    if ai_result.startswith("write "):
                        self.msg = ai_result  # 把大腦轉換好的完美格式，蓋掉使用者原本的碎碎念
                        self.write()          # 直接呼叫你剛剛改好 Bug 的寫入函式！
                        print("✨ AI 輔助記帳成功！")
                    else:
                        # 這是閒聊，直接把 AI 的回話傳給 LINE
                        self.api.reply_message(self.tk, TextSendMessage(text=ai_result))
                        
                except Exception as e:
                    print(f"Gemini 大腦當機了: {e}")
                    self.api.reply_message(self.tk, TextSendMessage(text="AI 助理大腦暫時連線失敗，請稍後再試！"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)