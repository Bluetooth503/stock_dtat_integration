# -*- coding: utf-8 -*-
from common import *
import tushare as ts
import schedule


# ================================= 定义初始变量 =================================
n_days = 3 # 分析个股N个交易日资金流向
wait_seconds = 600 # 等待时间
max_retries = 100  # 最大重试次数

# ================================= 读取配置文件 =================================
config = load_config()
token = config.get('tushare', 'token')
pro = ts.pro_api(token)
engine = create_engine(get_pg_connection_string(config))

# ================================= 配置日志 =================================
logger = setup_logger()

# ================================= 交易日相关 =================================
def is_trade_date(date_str):
    """判断是否为交易日"""
    calendar = pro.trade_cal(start_date=date_str, end_date=date_str)
    return calendar.iloc[0]['is_open'] == 1

def get_latest_trade_dates(end_date, n_days):
    """获取截至指定日期的最近N个交易日列表"""
    start_date = (datetime.strptime(end_date, '%Y%m%d') - timedelta(days=30)).strftime('%Y%m%d')
    calendar = pro.trade_cal(start_date=start_date, end_date=end_date)
    trade_dates = calendar[calendar['is_open'] == 1]['cal_date'].sort_values(ascending=False)
    return trade_dates[:n_days].tolist()

# ================================= 通用数据获取函数 =================================
def get_data_with_retry(
    data_type: str,
    today: str,
    max_retries: int,
    wait_seconds: int,
    api_func: Callable,
    process_func: Optional[Callable] = None,
    **kwargs
) -> Optional[pd.DataFrame]:
    """
    通用数据获取函数
    
    Args:
        data_type: 数据类型描述
        today: 当前日期
        max_retries: 最大重试次数
        wait_seconds: 重试等待时间
        api_func: API调用函数
        process_func: 数据处理函数（可选）
        **kwargs: 传递给process_func的额外参数
    
    Returns:
        Optional[pd.DataFrame]: 处理后的数据框或None
    """
    trade_dates = get_latest_trade_dates(today, n_days)
    logger.info(f"正在获取以下交易日的{data_type}数据: {trade_dates}")
    
    for retry in range(max_retries):
        all_data = []
        has_today_data = False
        
        for date in trade_dates:
            df = api_func(trade_date=date)
            if not df.empty:
                if process_func:
                    df = process_func(df, **kwargs)
                all_data.append(df)
                if date == today:
                    has_today_data = True
                # logger.debug(f"成功获取 {date} 的{data_type}数据")
            else:
                logger.warning(f"获取 {date} 的{data_type}数据为空")

        if has_today_data:
            result = pd.concat(all_data, ignore_index=True)
            return result.sort_values('trade_date', ascending=True)
            
        logger.warning(f"未获取到今日数据，等待{wait_seconds}秒后重试...")
        time.sleep(wait_seconds)
    
    logger.error(f"无法获取今日（{today}）{data_type}数据")
    return None

# ================================= 个股资金流向数据 =================================
def get_moneyflow_with_retry(today, max_retries, wait_seconds):
    """获取最近n_days的资金流向数据"""
    return get_data_with_retry(
        data_type="资金流向",
        today=today,
        max_retries=max_retries,
        wait_seconds=wait_seconds,
        api_func=pro.moneyflow
    )

# ================================= 同花顺行业资金流向数据 =================================
def get_industry_moneyflow_with_retry(today, max_retries, wait_seconds):
    """获取最近n_days的同花顺行业资金流向数据"""
    def process_industry_data(df):
        return df.rename(columns={'ts_code': 'industry_code'})
        
    return get_data_with_retry(
        data_type="同花顺行业资金流向",
        today=today,
        max_retries=max_retries,
        wait_seconds=wait_seconds,
        api_func=pro.moneyflow_ind_ths,
        process_func=process_industry_data
    )

# ================================= 个股daily_basic数据 =================================
def _get_circ_mv_range(circ_mv):
    """根据流通市值计算区间标签（内部函数）"""
    circ_mv = circ_mv / 10000  # circ_mv单位万元,转换为亿元
    if circ_mv <= 20:
        return '0-20亿'
    elif circ_mv <= 50:
        return '20-50亿'
    elif circ_mv <= 100:
        return '50-100亿'
    elif circ_mv <= 500:
        return '100-500亿'
    elif circ_mv <= 1000:
        return '500-1000亿'
    elif circ_mv <= 10000:
        return '1000-10000亿'
    else:
        return '10000亿以上'

def get_daily_basic_with_retry(today, max_retries, wait_seconds):
    """获取最近n_days的每日基本面数据"""
    def process_basic_data(df):
        df['circ_mv_range'] = df['circ_mv'].apply(_get_circ_mv_range)
        numeric_columns = ['close', 'turnover_rate', 'turnover_rate_f', 'volume_ratio', 
                         'pe', 'pe_ttm', 'pb', 'ps', 'ps_ttm', 'dv_ratio', 'dv_ttm',
                         'total_share', 'float_share', 'free_share', 'total_mv', 'circ_mv']
        for col in numeric_columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        return df
        
    return get_data_with_retry(
        data_type="每日基本面",
        today=today,
        max_retries=max_retries,
        wait_seconds=wait_seconds,
        api_func=pro.daily_basic,
        process_func=process_basic_data
    )

# ================================= 个股k线数据 =================================
def get_daily_k_with_retry(today, max_retries, wait_seconds):
    """获取最近n_days的日线行情数据"""
    return get_data_with_retry(
        data_type="日线行情",
        today=today,
        max_retries=max_retries,
        wait_seconds=wait_seconds,
        api_func=pro.daily
    )

# ================================= 个股资金流向得分计算 =================================
def _zscore_normalize_to_100(series):
    """将数据进行Z-score标准化，然后用sigmoid函数映射到0-100区间"""
    mean = series.mean()
    std = series.std()
    if std == 0:
        return pd.Series([50] * len(series), index=series.index)
    z_scores = (series - mean) / std
    normalized = 100 / (1 + np.exp(-z_scores))
    return normalized.round(4)

def calculate_stock_score(moneyflow_df, basic_df):
    """计算个股资金流向得分"""
    # 计算净流入
    net_flows = {
        "特大单净流入": moneyflow_df["buy_elg_amount"] - moneyflow_df["sell_elg_amount"],
        "大单净流入":   moneyflow_df["buy_lg_amount"]  - moneyflow_df["sell_lg_amount"],
        "中单净流入":   moneyflow_df["buy_md_amount"]  - moneyflow_df["sell_md_amount"],
        "小单净流入":   moneyflow_df["buy_sm_amount"]  - moneyflow_df["sell_sm_amount"]
    }
    moneyflow_df = moneyflow_df.assign(**net_flows)
    merged_df = pd.merge(moneyflow_df, basic_df, on=['ts_code','trade_date'], how='left')

    # 按股票代码分组,聚合计算多日指标
    agg_dict = {
        "特大单净流入": "sum",
        "大单净流入": "sum",
        "中单净流入": "sum",
        "小单净流入": "sum",
        "circ_mv": "last",
        "circ_mv_range": "last",
        "volume_ratio": "mean",
        "turnover_rate": "mean"
    }
    stock_data = merged_df.groupby("ts_code").agg(agg_dict).rename(columns={
        "特大单净流入": "特大单净流入总和",
        "大单净流入": "大单净流入总和",
        "中单净流入": "中单净流入总和",
        "小单净流入": "小单净流入总和",
        "circ_mv": "市值",
        "circ_mv_range": "市值区间",
        "volume_ratio": "量比均值",
        "turnover_rate": "换手率均值"
    }).reset_index()
    
    # 计算资金流向指标（原始值和Z-score标准化值）
    for flow_type in ['特大单', '大单', '中单', '小单']:
        col = f'{flow_type}/市值'
        stock_data[col] = (stock_data[f'{flow_type}净流入总和'] / stock_data['市值'] * 100).round(4)
        stock_data[f'{col}_Z'] = stock_data.groupby('市值区间')[col].apply(_zscore_normalize_to_100).reset_index(level=0, drop=True)
    
    # 对换手率和量比也进行分组Z-score标准化
    for metric in ['换手率均值', '量比均值']:
        stock_data[f'{metric}_Z'] = stock_data.groupby('市值区间')[metric].apply(_zscore_normalize_to_100).reset_index(level=0, drop=True)
    
    # 定义固定权重（使用标准化后的值计算得分）
    weights = {
        '特大单/市值_Z': 0.30,
        '大单/市值_Z': 0.25,
        '中单/市值_Z': 0.15,
        '小单/市值_Z': 0.10,
        '换手率均值_Z': 0.10,
        '量比均值_Z': 0.10
    }
    
    # 按市值区间分组对综合得分进行Z-score标准化
    stock_data['综合得分'] = sum(stock_data[col] * weight for col, weight in weights.items())
    stock_data['综合得分'] = stock_data.groupby('市值区间')['综合得分'].apply(_zscore_normalize_to_100).reset_index(level=0, drop=True)

    # 个股按得分排序
    stock_rank = stock_data.sort_values('综合得分', ascending=False).round(2)
    stock_rank['trade_date'] = moneyflow_df['trade_date'].iloc[-1]
    stock_rank['统计天数'] = n_days
    stock_rank = stock_rank.reset_index(drop=True)
    stock_rank.insert(0, '排名', range(1, len(stock_rank) + 1))
    
    # 使用SQL计算成交额,净流入分位数
    current_date = moneyflow_df['trade_date'].max()
    percentile_sql = f"""
    WITH 
    -- 计算当前n_days的均值
    current_means AS (
        SELECT 
            k.ts_code,
            AVG(k.amount) as current_amount_mean,
            AVG(m.net_mf_amount) as current_netflow_mean
        FROM a_stock_daily_k k
        LEFT JOIN a_stock_moneyflow m ON k.ts_code = m.ts_code AND k.trade_date = m.trade_date
        WHERE k.trade_date > TO_CHAR(TO_DATE('{current_date}', 'YYYYMMDD') - INTERVAL '{n_days} days', 'YYYYMMDD')
        AND  k.trade_date <= TO_CHAR(TO_DATE('{current_date}', 'YYYYMMDD'), 'YYYYMMDD')
        GROUP BY k.ts_code
    ),
    -- 计算分位数
    amount_percentiles AS (
        SELECT 
            cm.ts_code,
            NTILE(10) OVER (PARTITION BY k.ts_code ORDER BY k.amount) * 10 as amount_percentile
        FROM current_means cm
        LEFT JOIN a_stock_daily_k k ON k.ts_code = cm.ts_code
        WHERE k.trade_date > TO_CHAR(TO_DATE('{current_date}', 'YYYYMMDD') - INTERVAL '365 days', 'YYYYMMDD')
        AND  k.trade_date <= TO_CHAR(TO_DATE('{current_date}', 'YYYYMMDD'), 'YYYYMMDD')
        AND k.amount <= cm.current_amount_mean
    ),
    netflow_percentiles AS (
        SELECT 
            cm.ts_code,
            NTILE(10) OVER (PARTITION BY m.ts_code ORDER BY m.net_mf_amount) * 10 as netflow_percentile
        FROM current_means cm
        LEFT JOIN a_stock_moneyflow m ON m.ts_code = cm.ts_code
        WHERE m.trade_date > TO_CHAR(TO_DATE('{current_date}', 'YYYYMMDD') - INTERVAL '365 days', 'YYYYMMDD')
        AND  m.trade_date <= TO_CHAR(TO_DATE('{current_date}', 'YYYYMMDD'), 'YYYYMMDD')
        AND m.net_mf_amount <= cm.current_netflow_mean
    )
    SELECT 
        cm.ts_code,
        COALESCE(MAX(ap.amount_percentile), 0) as "成交额分位数",
        COALESCE(MAX(np.netflow_percentile), 0) as "净流入分位数"
    FROM current_means cm
    LEFT JOIN amount_percentiles ap ON cm.ts_code = ap.ts_code
    LEFT JOIN netflow_percentiles np ON cm.ts_code = np.ts_code
    GROUP BY cm.ts_code
    """
    percentile_df = pd.read_sql(percentile_sql, engine)
    stock_rank = pd.merge(stock_rank, percentile_df, on='ts_code', how='left')
    
    # 结果列包含原始值和归一化值
    result_columns = ['trade_date', 'ts_code', '统计天数', '排名', '市值区间', 
                     '特大单/市值', '特大单/市值_Z',
                     '大单/市值', '大单/市值_Z',
                     '中单/市值', '中单/市值_Z',
                     '小单/市值', '小单/市值_Z',
                     '换手率均值', '换手率均值_Z',
                     '量比均值', '量比均值_Z',
                     '成交额分位数', '净流入分位数',
                     '综合得分']
    return stock_rank[result_columns]

# ================================= 行业资金流向得分计算 =================================
def calculate_percentile(data: pd.Series, value: float, default: float = 50) -> float:
    """计算分位数，处理异常情况"""
    try:
        if len(data) > 1:
            return stats.percentileofscore(data, value)
        return default
    except Exception as e:
        logger.error(f"计算分位数时出错: {str(e)}")
        return default

def calculate_rolling_percentile(df: pd.DataFrame, group_col: str, value_col: str, 
                               window: int, min_periods: int = 1) -> float:
    """计算滚动均值的分位数"""
    try:
        df['rolling_mean'] = df[value_col].rolling(window=window, min_periods=min_periods).mean()
        if not df.empty and not df['rolling_mean'].isnull().all():
            latest_mean = df['rolling_mean'].dropna().iloc[0] if not df['rolling_mean'].dropna().empty else 0
            valid_means = df['rolling_mean'].dropna()
            return calculate_percentile(valid_means, latest_mean)
        return 50
    except Exception as e:
        logger.error(f"计算滚动分位数时出错: {str(e)}")
        return 50

def calculate_industry_score(industry_moneyflow_df):
    """计算行业资金流向得分"""
    try:
        latest_date = industry_moneyflow_df['trade_date'].max()
        
        # 获取历史数据
        sql = """
        SELECT * FROM a_stock_moneyflow_industry_ths
        WHERE trade_date >= TO_CHAR(TO_DATE(%s, 'YYYYMMDD') - INTERVAL '365 days', 'YYYYMMDD')
        AND trade_date <= %s
        """
        df = pd.read_sql(sql, engine, params=(latest_date, latest_date))
        
        # 处理当日数据
        current_day = df[df['trade_date'] == latest_date].copy()
        if 'net_amount' not in current_day.columns or current_day['net_amount'].isnull().all():
            if 'net_amount' in industry_moneyflow_df.columns:
                current_day = industry_moneyflow_df[industry_moneyflow_df['trade_date'] == latest_date].copy()
        
        current_day['净额(亿元)'] = current_day['net_amount'].round(2)
        
        # 计算当日净额的历史分位数
        industry_groups = df.groupby('industry')
        percentile_ranks = []
        
        for industry in current_day['industry'].unique():
            try:
                industry_data = industry_groups.get_group(industry)
                current_value = current_day[current_day['industry'] == industry]['net_amount'].iloc[0]
                percentile = calculate_percentile(industry_data['net_amount'], current_value)
                percentile_ranks.append({
                    'industry': industry,
                    'percentile_rank': percentile
                })
            except Exception as e:
                logger.error(f"计算行业 {industry} 的分位数时出错: {str(e)}")
                percentile_ranks.append({
                    'industry': industry,
                    'percentile_rank': 50
                })
        
        current_day = current_day.merge(pd.DataFrame(percentile_ranks), on='industry', how='left')
        
        # 计算过去N日均值及其分位数
        past_days_data = df[df['trade_date'] < latest_date].copy()
        past_days_data = past_days_data.sort_values(['industry', 'trade_date'], ascending=[True, False])
        
        for days in range(1, 6):
            past_days_percentiles = []
            for industry in current_day['industry'].unique():
                try:
                    industry_data = past_days_data[past_days_data['industry'] == industry]
                    percentile = calculate_rolling_percentile(
                        industry_data, 'industry', 'net_amount', days
                    )
                    past_days_percentiles.append({
                        'industry': industry,
                        f'过去{days}日分位数': percentile
                    })
                except Exception as e:
                    logger.error(f"计算行业 {industry} 的过去{days}日分位数时出错: {str(e)}")
                    past_days_percentiles.append({
                        'industry': industry,
                        f'过去{days}日分位数': 50
                    })
            
            current_day = current_day.merge(pd.DataFrame(past_days_percentiles), on='industry', how='left')
        
        # 整理最终结果
        result_columns = ['trade_date', 'industry_code', 'industry', '净额(亿元)', 'percentile_rank',
                         '过去5日分位数', '过去4日分位数', '过去3日分位数', '过去2日分位数', '过去1日分位数']
        
        # 确保所有列都存在
        for col in result_columns:
            if col not in current_day.columns:
                if col == 'percentile_rank':
                    current_day[col] = 50
                elif col.startswith('过去') and col.endswith('分位数'):
                    current_day[col] = 50
                else:
                    current_day[col] = None
        
        result = current_day[result_columns].copy()
        result = result.rename(columns={'percentile_rank': '净额分位数'})
        result = result.sort_values('净额分位数', ascending=False)
        result.insert(3, '排名', range(1, len(result) + 1))
        
        return result
        
    except Exception as e:
        logger.error(f"计算行业资金流向得分时出错: {str(e)}")
        raise

# ================================= 每日执行任务 =================================
def _save_to_database(df: pd.DataFrame, table_name: str, temp_table: str, 
                    conflict_columns: list, data_type: str) -> bool:
    """    
    Args:
        df: 要保存的数据框
        table_name: 目标表名
        temp_table: 临时表名
        conflict_columns: 用于处理冲突的列名列表
        data_type: 数据类型描述（用于日志）
    Returns:
        bool: 是否保存成功
    """
    try:
        insert_sql = f"""
            INSERT INTO {table_name}
            SELECT * FROM {temp_table}
            ON CONFLICT ({', '.join(conflict_columns)}) DO NOTHING
        """
        upsert_data(df, table_name, temp_table, insert_sql, engine)
        # logger.info(f"{data_type}数据已成功保存")
        return True
    except Exception as e:
        logger.error(f"保存{data_type}数据时出错: {str(e)}")
        return False
    
def daily_task():
    """每日执行的任务"""
    today = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
    
    if not is_trade_date(today):
        logger.info(f"{today} 不是交易日，跳过执行")
        return
    
    # 获取资金流向数据
    logger.info(f"开始获取最近{n_days}天的资金流向数据...")
    moneyflow_df = get_moneyflow_with_retry(today, max_retries, wait_seconds)
    if moneyflow_df is None:
        logger.error(f"无法获取完整的资金流向数据，请检查数据源")
        return
    
    if not _save_to_database(
        moneyflow_df, 
        'a_stock_moneyflow', 
        'temp_moneyflow',
        ['ts_code', 'trade_date'],
        '资金流向'
    ):
        return
        
    # 获取同花顺行业资金流向数据
    logger.info(f"开始获取最近{n_days}天的同花顺行业资金流向数据...")
    industry_moneyflow_df = get_industry_moneyflow_with_retry(today, max_retries, wait_seconds)
    if industry_moneyflow_df is None:
        logger.error(f"无法获取完整的同花顺行业资金流向数据，请检查数据源")
        return
    
    if not _save_to_database(
        industry_moneyflow_df,
        'a_stock_moneyflow_industry_ths',
        'temp_industry_moneyflow',
        ['trade_date', 'industry_code'],
        '同花顺行业资金流向'
    ):
        return

    # 获取每日基本面数据
    logger.info(f"开始获取最近{n_days}天的每日基本面数据...")
    basic_df = get_daily_basic_with_retry(today, max_retries, wait_seconds)
    if basic_df is None:
        logger.error(f"无法获取完整的每日基本面数据，请检查数据源")
        return
    
    if not _save_to_database(
        basic_df,
        'a_stock_daily_basic',
        'temp_daily_basic',
        ['ts_code', 'trade_date'],
        '每日基本面'
    ):
        return
        
    # 获取日线行情数据
    logger.info(f"开始获取最近{n_days}天的日线行情数据...")
    daily_k_df = get_daily_k_with_retry(today, max_retries, wait_seconds)
    if daily_k_df is None:
        logger.error(f"无法获取完整的日线行情数据，请检查数据源")
        return
    
    if not _save_to_database(
        daily_k_df,
        'a_stock_daily_k',
        'temp_daily_k',
        ['ts_code', 'trade_date'],
        '日线行情'
    ):
        return
        
    # 计算个股资金流向得分
    logger.info(f"开始计算最近{n_days}天的个股资金流向得分...")
    try:
        stock_rank = calculate_stock_score(moneyflow_df, basic_df)
        if not _save_to_database(
            stock_rank,
            'a_stock_moneyflow_score',
            'temp_moneyflow_score',
            ['ts_code', 'trade_date'],
            '个股资金流向得分'
        ):
            return
    except Exception as e:
        logger.error(f"计算个股资金流向得分时出错: {e}")
        return

    # 计算行业资金流向得分
    logger.info(f"开始计算最近{n_days}天的行业资金流向得分...")
    try:
        industry_rank = calculate_industry_score(industry_moneyflow_df)
        if not _save_to_database(
            industry_rank,
            'a_stock_moneyflow_industry_score',
            'temp_industry_moneyflow_score',
            ['industry_code', 'trade_date'],
            '行业资金流向得分'
        ):
            return
    except Exception as e:
        logger.error(f"计算行业资金流向得分时出错: {e}")
        return

def main():
    """主函数"""
    schedule.every().day.at("16:30").do(daily_task)
    
    logger.info("定时任务已启动，将在每个交易日下午16:30执行...")
    while True:
        schedule.run_pending()
        time.sleep(600)

if __name__ == "__main__":
    main()



