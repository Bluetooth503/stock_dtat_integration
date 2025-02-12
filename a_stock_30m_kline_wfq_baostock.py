# -*- coding: utf-8 -*-
from common import *
import baostock as bs


# 定义初始变量
stock_list = pd.read_csv('沪深A股_stock_list.csv', header=None, names=['ts_code'])
start_time = '20000101'
end_time   = datetime.today().strftime('%Y%m%d')
period     = '30m'
table_name = 'a_stock_30m_kline_wfq_baostock'
tmp_table  = f"temp_{table_name}_{int(time.time())}"


def create_table_if_not_exists(engine) -> None:
    """创建数据表（如果不存在）"""
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS a_stock_30m_kline_wfq_baostock (
        trade_time TIMESTAMP NOT NULL,
        ts_code VARCHAR(20) NOT NULL,
        open NUMERIC(18, 4),
        high NUMERIC(18, 4),
        low NUMERIC(18, 4),
        close NUMERIC(18, 4),
        volume NUMERIC(18, 4),
        amount NUMERIC(18, 4),
        adjust_flag INTEGER,
        PRIMARY KEY (trade_time, ts_code)
    );
    
    -- 创建索引以提升查询性能
    CREATE INDEX IF NOT EXISTS idx_30m_kline_wfq_baostock_ts_code 
    ON a_stock_30m_kline_wfq_baostock(ts_code);
    """
    with engine.begin() as conn:
        conn.execute(text(create_table_sql))

def get_latest_record_time(engine) -> str:
    """获取数据库中最新的记录时间，如果表不存在或为空则返回19900101"""
    query = f"""
        SELECT MAX(trade_time) 
        FROM {table_name}
        WHERE trade_time IS NOT NULL
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(text(query)).scalar()
            return result.strftime('%Y%m%d') if result else '19900101'
    except:
        return '19900101'

def download_30min_kline(ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """下载指定股票的30分钟K线数据"""
    bs_code = convert_to_baostock_code(ts_code)
    
    # 转换日期格式为 baostock 所需的格式
    bs_start_date = convert_date_format(start_date)
    bs_end_date = convert_date_format(end_date)
    
    rs = bs.query_history_k_data_plus(
        code = bs_code,
        fields = "date,time,code,open,high,low,close,volume,amount,adjustflag",
        start_date = bs_start_date,
        end_date = bs_end_date,
        frequency = "30",
        adjustflag = "3"  # 不复权
    )
    
    if rs.error_code != '0':
        print(f"下载 {ts_code} 数据时出错: {rs.error_msg}")
        return pd.DataFrame()
    
    data_list = []
    while (rs.error_code == '0') & rs.next():
        data_list.append(rs.get_row_data())
        
    if not data_list:
        return pd.DataFrame()
        
    df = pd.DataFrame(data_list, columns=rs.fields)
    
    # 转换股票代码格式
    df['code'] = df['code'].apply(convert_to_tushare_code)
    
    # 合并日期和时间列创建trade_time
    df['time'] = df['time'].apply(format_time)
    df['trade_time'] = pd.to_datetime(df['date'] + ' ' + df['time'], format='%Y-%m-%d %H:%M:%S')
    
    # 重命名列以匹配数据库表结构
    df = df.rename(columns={'code': 'ts_code', 'adjustflag': 'adjust_flag'})
    
    # 选择需要的列并转换数据类型
    result_df = df[['trade_time', 'ts_code', 'open', 'high', 'low', 'close', 'volume', 'amount', 'adjust_flag']].copy()
    
    # 转换数据类型
    numeric_columns = ['open', 'high', 'low', 'close', 'volume', 'amount']
    result_df[numeric_columns] = result_df[numeric_columns].apply(pd.to_numeric)
    result_df['adjust_flag'] = result_df['adjust_flag'].astype(int)
        
    return result_df

def main():
    """主函数"""
    # 初始化数据库连接
    config = load_config()
    engine = create_engine(get_pg_connection_string(config))
    
    # 确保表存在
    create_table_if_not_exists(engine)
    
    # 登录 baostock
    bs.login()
    
    # 获取最新记录时间
    latest_time = get_latest_record_time(engine)
    print(f"数据库最新记录时间: {latest_time}")
    
    # 获取股票列表
    stocks = stock_list['ts_code'].tolist()
    
    # 用于存储所有数据
    all_data = []
    
    for ts_code in tqdm(stocks, desc = '开始下载数据'):        
        # 下载新数据
        df = download_30min_kline(ts_code = ts_code, start_date = latest_time, end_date = end_time)
        
        if not df.empty:
            all_data.append(df)
        else:
            print(f"{ts_code} 没有新数据")
    
    # 合并所有数据
    if all_data:
        final_df = pd.concat(all_data, ignore_index=True)
        print(f"共下载 {len(final_df)} 条记录")
        
        # 使用 upsert_data 保存到数据库
        insert_sql = f"""
            INSERT INTO {table_name} (trade_time, ts_code, open, high, low, close, volume, amount, adjust_flag)
            SELECT trade_time, ts_code, open, high, low, close, volume, amount, adjust_flag
            FROM {tmp_table} ON CONFLICT (trade_time, ts_code) DO NOTHING;
        """
        
        upsert_data(final_df, table_name, tmp_table, insert_sql, engine)
        print("数据保存完成")
    
    # 登出 baostock
    bs.logout()

if __name__ == "__main__":
    main()