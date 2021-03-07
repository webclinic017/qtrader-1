# -*- coding: utf-8 -*-
# @Time    : 13/9/2020 12:33 PM
# @Author  : Joseph Chen
# @Email   : josephchenhk@gmail.com
# @FileName: bondsim_server.py
# @Software: PyCharm
import os
import socket
import pytz
import pandas as pd
from datetime import datetime
import json
import time
import threading
import ast
from dateutil.parser import parse
from quantkits.logger import logger

from qtrader.setting import FEATHER_DATA_PATH
from qtrader.data_adaptors.feather_adapter import FeatherDataHandler
from qtrader.core.utility import main_path

os.chdir(main_path)

with open(f'setting/connect_bondsim.json') as json_file:
    setting = json.load(json_file)
    HOST = setting["地址"]
    PORT = setting["端口"]


def get_isins(issuer: str) -> list:
    """Get vanilla bonds from the same issuer."""
    if issuer=="Treasury_GT":
        df = pd.read_excel("{}GT_bonds_check.xlsx".format(FEATHER_DATA_PATH))
        df = df[(df["Issuer Name"] == 'United States Treasury Note/Bond') & (df["Data Available"] == "yes")]
    elif issuer=="Treasury_GB":
        df = pd.read_excel("{}GB_bonds_check.xlsx".format(FEATHER_DATA_PATH))
        df = df[(df["Issuer Name"] == "United States Treasury Bill") & (df["Data Available"] == "yes")]
    else:
        df = pd.read_excel("{}real_estate_bonds_check.xlsx".format(FEATHER_DATA_PATH))
        df = df[(df["Issuer Name"] == issuer)
                & (df["Maturity Type"] == "AT MATURITY")
                & (df["Data Available"] == "yes")]
    isins = df["ISIN"].to_list()
    #isins = isins[:30] #test
    return isins


def get_factsheet(symbol:str, issuer:str)->dict:
    """Get bond information such as maturity date, issue date, and coupon."""
    if issuer=="Treasury_GT":
        df = pd.read_excel("{}GT_bonds_check.xlsx".format(FEATHER_DATA_PATH))
    elif issuer=="Treasury_GB":
        df = pd.read_excel("{}GB_bonds_check.xlsx".format(FEATHER_DATA_PATH))
    else:
        df = pd.read_excel("{}/real_estate_bonds_check.xlsx".format(FEATHER_DATA_PATH))
    df = df[df["ISIN"] == symbol]
    if df.empty:
        return None

    df = df[["Issue Date", "Maturity", "Cpn Freq Des", "Cpn"]]
    res = dict(zip(df.columns, df.iloc[0].values))
    res["Maturity"] = datetime.strptime(res["Maturity"], "%m/%d/%Y")
    res["Issue Date"] = datetime.strptime(res["Issue Date"], "%m/%d/%Y")
    tz = pytz.timezone("Asia/Hong_Kong")
    res["Maturity"] = tz.localize(res["Maturity"])
    res["Issue Date"] = tz.localize(res["Issue Date"])
    return res


def prepare_data(
        issuer: str,
        isins: list,
        start: datetime,
        end: datetime
    ) -> tuple:
    """"""
    source = "{}/{}".format(FEATHER_DATA_PATH, issuer)
    handlers = {}
    nodata_isins = []
    logger.info("Start to prepare data ... ")
    for isin in isins:
        ticker = "{}@BGN Corp".format(isin)
        handler = FeatherDataHandler(source, ticker, start, end)
        # validate data availability
        has_data = handler.check_data_availability()
        if not has_data:
            nodata_isins.append(isin)
        handler = FeatherDataHandler(source, ticker, start, end)
        handlers["{}_handler".format(isin)] = handler

    # Remove tickers without data
    eff_isins = list(set(isins) - set(nodata_isins))
    return eff_isins, handlers


class SimulatorServer:

    HOST = HOST        # Standard loopback interface address (localhost)
    PORT = PORT        # Port to listen on (non-privileged ports are > 1023)

    def __init__(self, cur_time:datetime, bond_data:dict):
        self.cur_time = cur_time

        # self.symbols = symbols
        # self.handlers = handlers
        # self.factsheets = factsheets
        self.bond_data = bond_data

        self.subscribed_symbols = []
        # self.last_snapshot_cache = {}
        # self.snapshot_cache = {}
        # print("开始校准历史数据时间戳 ...")
        # self.generate_snapshot() # 先将时间校准至当前时间cur_time
        # print("历史数据校准完毕")

    def subscribe(self, symbols:list):
        """ 订阅市场数据 """
        available_symbols = []
        self.subscribed_bond_data = {}
        for symbol in symbols:
            print()

    def update_timer(self, time: datetime):
        """ 更新时钟，据此分发市场快照数据 """
        self.cur_time = time

    def generate_snapshots(self):
        """ 负责分发市场数据 """
        snapshots = {issuer:{} for issuer in self.bond_data}
        # TODO(joseph): this should be subscribed_symbols
        for issuer in self.bond_data:
            symbols = self.bond_data[issuer]["isins"]
            handlers = self.bond_data[issuer]["handlers"]
            for symbol in symbols:
                handler = handlers.get(f"{symbol}_handler")
                snapshots[issuer][symbol] = handler.read_snapshot(self.cur_time)
        self.snapshots = snapshots
        return snapshots

    def start_server(self):
        """ 另起独立线程处理连接"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((self.HOST, self.PORT))
            s.listen()
            print("等待client接入 ...")
            conn, addr = s.accept()   # accept是block函数，直到client运行connect以后才会运行
            with conn:                # conn是新建立的socket连接，该连接表示与客户端相连的连接
                print('Connected by', addr)
                while True:
                    data = conn.recv(1024)     # recv是block函数，直到接收到数据以后才会运行
                    print(data)
                    if not data:
                        # break
                        continue
                    data_dict = ast.literal_eval(data.decode("utf-8"))
                    if data_dict["type"] == "TIMER":  # 每次收到更新timer信号，推送市场快照
                        timer = parse(data_dict["data"])
                        self.update_timer(timer)
                        snapshot = self.generate_snapshots()
                        snapshot_data = {}
                        for symbol, tick in snapshot.items():
                            tick_data = {k:v for k,v in tick.items() if k!="datetime"}
                            tick_data.update(dict(datetime=tick["datetime"].isoformat()))
                            snapshot_data[symbol] = tick_data
                        ret_snapshot = {"type":"SNAPSHOT", "data":snapshot_data}
                        ret_snapshot = json.dumps(ret_snapshot) + "\n"  # 以换行符作结束信号
                        conn.sendall(bytes(ret_snapshot, 'utf-8'))


    def start(self):
        """"""
        t = threading.Thread(target=self.start_server)
        t.setDaemon(True)
        t.setName("SimServerThread")
        t.start()
        while True:
            time.sleep(1)


if __name__=="__main__":
    START_DATE = datetime(2020, 6, 17, 0, 0, 0)
    END_DATE = datetime(2020, 6, 19, 0, 0, 10)
    TIME_ZONE = "Asia/Hong_Kong"
    tz = pytz.timezone(TIME_ZONE)
    start = tz.localize(START_DATE)
    end = tz.localize(END_DATE)
    issuers = ["Greenland Global Investment Ltd", "China Evergrande Group"]

    bond_data = {}
    for issuer in issuers:
        bond_data[issuer] = {}
        isins = get_isins(issuer)
        eff_isins, handlers = prepare_data(issuer=issuer, isins=isins, start=start, end=end)
        bond_factsheets = {}
        for isn in eff_isins:
            bond_factsheets[isn] = get_factsheet(symbol=isn, issuer=issuer)
        bond_data[issuer]["isins"] = eff_isins
        bond_data[issuer]["handlers"] = handlers
        bond_data[issuer]["factsheets"] = bond_factsheets

    print()
    s = SimulatorServer(bond_data=bond_data)
    # s.start()
