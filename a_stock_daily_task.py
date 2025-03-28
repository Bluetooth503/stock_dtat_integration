# -*- coding: utf-8 -*-
from common import *
import tushare as ts
import schedule

# ================================= 定义初始变量 =================================
n_days = 3          # 分析个股N个交易日累积资金流向(包含当日)
wait_seconds = 600  # 等待时间
max_retries = 100   # 最大重试次数
n = 0               # 当日偏移量,计算旧数据
run_time = "16:30"  # 运行时间
sleep_seconds = 600 # 等待时间

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

# ================================= 指数每日指标数据 =================================
def get_index_dailybasic_with_retry(today, max_retries, wait_seconds):
    """获取最近n_days的指数每日指标数据"""
    indices = [
        ('000300.SH', '沪深300'),
        ('000905.SH', '中证500'),
        ('399005.SZ', '中小100'),
        ('399006.SZ', '创业板指')
    ]
    
    def get_index_data(trade_date):
        all_index_data = []
        for index_code, index_name in indices:
            df = pro.index_dailybasic(ts_code=index_code, trade_date=trade_date)
            if not df.empty:
                df = df.rename(columns={'ts_code': 'index_code'})
                df['index_name'] = index_name
                all_index_data.append(df)
        if all_index_data:
            return pd.concat(all_index_data, ignore_index=True)
        return pd.DataFrame()
    
    return get_data_with_retry(
        data_type="指数每日指标",
        today=today,
        max_retries=max_retries,
        wait_seconds=wait_seconds,
        api_func=get_index_data
    )

# ================================= 指数市值得分计算 =================================
def calculate_index_marketvalue_score(index_dailybasic_df):
    """计算指数市值和换手率分位数得分"""
    try:
        latest_date = index_dailybasic_df['trade_date'].max()
        
        # 获取历史数据
        sql = """
        SELECT * FROM a_stock_index_dailybasic
        WHERE trade_date >= TO_CHAR(TO_DATE(%s, 'YYYYMMDD') - INTERVAL '365 days', 'YYYYMMDD')
        AND trade_date <= %s
        """
        df = pd.read_sql(sql, engine, params=(latest_date, latest_date))
        
        # 处理当日数据
        current_day = df[df['trade_date'] == latest_date].copy()
        if current_day.empty:
            current_day = index_dailybasic_df[index_dailybasic_df['trade_date'] == latest_date].copy()
        
        # 先将所有数据的float_mv转换为亿元
        df['float_mv'] = df['float_mv'] / 100000000  # 转换历史数据
        current_day['float_mv'] = current_day['float_mv'] / 100000000  # 转换当前数据
        
        # 计算流通市值（亿元）和平均价格
        current_day['流通市值(亿)'] = current_day['float_mv'].round(2)
        current_day['平均价格'] = (current_day['float_mv'] * 100000000 / current_day['float_share']).round(2)  # 转回元计算平均价格
        
        # 计算分位数
        percentile_ranks = []
        for index_code in current_day['index_code'].unique():
            try:
                # 获取该指数的历史数据
                index_history = df[df['index_code'] == index_code]
                current_index = current_day[current_day['index_code'] == index_code].iloc[0]
                
                # 计算流通市值分位数（已经是亿元单位了）
                if not index_history.empty:
                    float_mv_percentile = stats.percentileofscore(index_history['float_mv'].values, 
                                                                current_index['float_mv'])
                else:
                    float_mv_percentile = 50
                    
                # 计算换手率分位数
                if not index_history.empty:
                    turnover_percentile = stats.percentileofscore(index_history['turnover_rate'].values, 
                                                                current_index['turnover_rate'])
                else:
                    turnover_percentile = 50
                
                percentile_ranks.append({
                    'index_code': index_code,
                    'index_name': current_index['index_name'],
                    'trade_date': latest_date,
                    '流通市值(亿)': current_index['流通市值(亿)'],
                    '流通市值分位数': round(float_mv_percentile, 2),
                    '换手率(%)': round(current_index['turnover_rate'], 2),
                    '换手率分位数': round(turnover_percentile, 2),
                    '平均价格': current_index['平均价格']
                })
                
            except Exception as e:
                logger.error(f"计算指数 {index_code} 的分位数时出错: {str(e)}")
                continue
        
        if not percentile_ranks:
            logger.error("没有计算出任何指数的分位数")
            return None
            
        result = pd.DataFrame(percentile_ranks)
        
        # 按流通市值分位数降序排序
        result = result.sort_values('流通市值分位数', ascending=False)
        
        # 确保列的顺序正确
        result_columns = ['index_code', 'index_name', 'trade_date', 
                         '流通市值(亿)', '流通市值分位数', 
                         '换手率(%)', '换手率分位数',
                         '平均价格']
        
        return result[result_columns]
        
    except Exception as e:
        logger.error(f"计算指数市值得分时出错: {str(e)}")
        return None

# ================================= 市场资金净流入分位数计算 =================================
def calculate_market_moneyflow_score(industry_moneyflow_df):
    """计算市场资金净流入分位数"""
    try:
        latest_date = industry_moneyflow_df['trade_date'].max()
        
        # 获取历史数据
        sql = """
        SELECT trade_date, SUM(net_amount) as total_net_amount 
        FROM a_stock_moneyflow_industry_ths
        WHERE trade_date >= TO_CHAR(TO_DATE(%s, 'YYYYMMDD') - INTERVAL '365 days', 'YYYYMMDD')
        AND trade_date <= %s
        GROUP BY trade_date
        ORDER BY trade_date DESC
        """
        df = pd.read_sql(sql, engine, params=(latest_date, latest_date))
        
        # 处理当日数据
        current_day = df[df['trade_date'] == latest_date].copy()
        if current_day.empty:
            # 如果当日数据不在历史数据中，从传入的DataFrame计算
            current_net = industry_moneyflow_df[industry_moneyflow_df['trade_date'] == latest_date]['net_amount'].sum()
            current_day = pd.DataFrame([{
                'trade_date': latest_date,
                'total_net_amount': current_net
            }])
        
        # 计算净值（亿元）- net_amount已经是亿元单位
        current_day['净值(亿元)'] = current_day['total_net_amount'].round(3)
        
        # 计算分位数
        try:
            if not df.empty:
                net_percentile = stats.percentileofscore(df['total_net_amount'].values, 
                                                       current_day['total_net_amount'].iloc[0])
            else:
                net_percentile = 50
                
            result = pd.DataFrame({
                'trade_date': [latest_date],
                '净值(亿元)': current_day['净值(亿元)'].values,
                '净值分位数': [round(net_percentile, 2)]
            })
            
            return result
            
        except Exception as e:
            logger.error(f"计算市场资金净流入分位数时出错: {str(e)}")
            return None
        
    except Exception as e:
        logger.error(f"计算市场资金净流入分位数时出错: {str(e)}")
        return None

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

def calculate_stock_score(moneyflow_df, basic_df, daily_k_df):
    """计算个股资金流向得分"""
    try:
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
            "volume_ratio": lambda x: x.ewm(span=n_days).mean().iloc[-1],
            "turnover_rate": lambda x: x.ewm(span=n_days).mean().iloc[-1]
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
            '特大单/市值_Z': -0.17,
            '大单/市值_Z': -0.15,
            '中单/市值_Z': -0.17,
            '小单/市值_Z': -0.36,
            '换手率均值_Z': 0.05,
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
        
        # 使用已有数据计算最近n_days的均值
        recent_amount_df = daily_k_df.groupby('ts_code')['amount'].apply(lambda x: x.ewm(span=n_days).mean().iloc[-1]).reset_index()
        recent_amount_df.rename(columns={'amount': 'amount_mean'}, inplace=True)

        recent_netflow_df = moneyflow_df.groupby('ts_code')['net_mf_amount'].apply(lambda x: x.ewm(span=n_days).mean().iloc[-1]).reset_index()
        recent_netflow_df.rename(columns={'net_mf_amount': 'netflow_mean'}, inplace=True)

        # 查询历史数据
        current_date = moneyflow_df['trade_date'].iloc[-1]
        history_sql = f"""
        SELECT ts_code, trade_date, amount 
        FROM a_stock_daily_k 
        WHERE trade_date > TO_CHAR(TO_DATE('{current_date}', 'YYYYMMDD') - INTERVAL '365 days', 'YYYYMMDD')
        AND trade_date <= '{current_date}'
        """
        amount_history_df = pd.read_sql(history_sql, engine)

        # 查询净流入历史数据
        netflow_sql = f"""
        SELECT ts_code, trade_date, net_mf_amount 
        FROM a_stock_moneyflow 
        WHERE trade_date > TO_CHAR(TO_DATE('{current_date}', 'YYYYMMDD') - INTERVAL '365 days', 'YYYYMMDD')
        AND trade_date <= '{current_date}'
        """
        netflow_history_df = pd.read_sql(netflow_sql, engine)

        # 优化：预先分组数据，避免在循环中重复过滤
        amount_history_grouped = dict(list(amount_history_df.groupby('ts_code')))
        netflow_history_grouped = dict(list(netflow_history_df.groupby('ts_code')))

        # 创建ts_code到均值的映射，避免在循环中查找
        amount_mean_dict = dict(zip(recent_amount_df['ts_code'], recent_amount_df['amount_mean']))
        netflow_mean_dict = dict(zip(recent_netflow_df['ts_code'], recent_netflow_df['netflow_mean']))

        # 计算分位数
        percentile_results = []

        # 使用分批处理来减少内存压力
        batch_size = 1000
        ts_codes = recent_amount_df['ts_code'].unique()
        total_stocks = len(ts_codes)

        for i in range(0, total_stocks, batch_size):
            batch_ts_codes = ts_codes[i:min(i+batch_size, total_stocks)]
            
            batch_results = []
            for ts_code in batch_ts_codes:
                # 成交额分位数
                ts_amount_history = amount_history_grouped.get(ts_code, pd.DataFrame())
                if not ts_amount_history.empty and ts_code in amount_mean_dict:
                    ts_amount_values = ts_amount_history['amount'].values
                    ts_amount_mean = amount_mean_dict[ts_code]
                    amount_percentile = stats.percentileofscore(ts_amount_values, ts_amount_mean)
                else:
                    amount_percentile = 50
                
                # 净流入分位数 - 使用正确的净流入数据
                ts_netflow_history = netflow_history_grouped.get(ts_code, pd.DataFrame())
                if not ts_netflow_history.empty and ts_code in netflow_mean_dict:
                    ts_netflow_values = ts_netflow_history['net_mf_amount'].values
                    ts_netflow_mean = netflow_mean_dict[ts_code]
                    netflow_percentile = stats.percentileofscore(ts_netflow_values, ts_netflow_mean)
                else:
                    netflow_percentile = 50
                
                batch_results.append({
                    'ts_code': ts_code,
                    '成交额分位数': round(amount_percentile, 2),
                    '净流入分位数': round(netflow_percentile, 2)
                })
            
            percentile_results.extend(batch_results)

        percentile_df = pd.DataFrame(percentile_results)
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
    except Exception as e:
        logger.error(f"计算个股资金流向得分时出错: {e}")
        return None


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
        
        # 计算过去N日分位数
        past_days_data = df[df['trade_date'] < latest_date].copy()  # 不包含当天
        past_days_data = past_days_data.sort_values(['industry', 'trade_date'], ascending=[True, False])
        
        for days in range(1, 6):
            past_days_percentiles = []
            for industry in current_day['industry'].unique():
                try:
                    industry_data = past_days_data[past_days_data['industry'] == industry]
                    
                    # 获取第N天的数据（比如过去5日，就是第5天的值）
                    if len(industry_data) >= days:
                        target_day_value = industry_data.iloc[days-1]['net_amount']  # 获取第N天的净额
                        # 使用全部历史数据计算分位数
                        all_history_data = industry_data['net_amount']
                        if not all_history_data.empty:
                            percentile = calculate_percentile(all_history_data, target_day_value)
                        else:
                            percentile = 50
                    else:
                        percentile = 50
                        
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
def daily_task():
    """每日执行的任务"""
    today = (datetime.now() - timedelta(days=n)).strftime('%Y%m%d')
    
    if not is_trade_date(today):
        logger.info(f"{today} 不是交易日，跳过执行")
        return
    
    # 获取资金流向数据
    logger.info(f"开始获取最近{n_days}天的资金流向数据...")
    moneyflow_df = get_moneyflow_with_retry(today, max_retries, wait_seconds)
    if moneyflow_df is None:
        logger.error(f"无法获取完整的资金流向数据，请检查数据源")
        return
    
    if not save_to_database(
        df=moneyflow_df, 
        table_name='a_stock_moneyflow', 
        conflict_columns=['ts_code', 'trade_date'],
        update_columns=['buy_sm_vol','buy_sm_amount','sell_sm_vol','sell_sm_amount','buy_md_vol','buy_md_amount','sell_md_vol','sell_md_amount','buy_lg_vol','buy_lg_amount','sell_lg_vol','sell_lg_amount','buy_elg_vol','buy_elg_amount','sell_elg_vol','sell_elg_amount','net_mf_vol','net_mf_amount'],
        data_type='资金流向',
        engine=engine
    ):
        return
        
    # 获取同花顺行业资金流向数据
    logger.info(f"开始获取最近{n_days}天的同花顺行业资金流向数据...")
    industry_moneyflow_df = get_industry_moneyflow_with_retry(today, max_retries, wait_seconds)
    if industry_moneyflow_df is None:
        logger.error(f"无法获取完整的同花顺行业资金流向数据，请检查数据源")
        return
    
    if not save_to_database(
        df=industry_moneyflow_df,
        table_name='a_stock_moneyflow_industry_ths',
        conflict_columns=['trade_date', 'industry_code'],
        update_columns=['industry','lead_stock','close','pct_change','company_num','pct_change_stock','close_price','net_buy_amount','net_sell_amount','net_amount'],
        data_type='同花顺行业资金流向',
        engine=engine
    ):
        return

    # 获取每日基本面数据
    logger.info(f"开始获取最近{n_days}天的每日基本面数据...")
    basic_df = get_daily_basic_with_retry(today, max_retries, wait_seconds)
    if basic_df is None:
        logger.error(f"无法获取完整的每日基本面数据，请检查数据源")
        return
    
    if not save_to_database(
        df=basic_df,
        table_name='a_stock_daily_basic',
        conflict_columns=['ts_code', 'trade_date'],
        update_columns=['close','turnover_rate','turnover_rate_f','volume_ratio','pe','pe_ttm','pb','ps','ps_ttm','dv_ratio','dv_ttm','total_share','float_share','free_share','total_mv','circ_mv','circ_mv_range'],
        data_type='每日基本面',
        engine=engine
    ):
        return
        
    # 获取日线行情数据
    logger.info(f"开始获取最近{n_days}天的日线行情数据...")
    daily_k_df = get_daily_k_with_retry(today, max_retries, wait_seconds)
    if daily_k_df is None:
        logger.error(f"无法获取完整的日线行情数据，请检查数据源")
        return
    
    if not save_to_database(
        df=daily_k_df,
        table_name='a_stock_daily_k',
        conflict_columns=['ts_code', 'trade_date'],
        update_columns=['open','high','low','close','pre_close','change','pct_chg','vol','amount'],
        data_type='日线行情',
        engine=engine
    ):
        return

    # 获取指数每日指标数据
    logger.info(f"开始获取最近{n_days}天的指数每日指标数据...")
    index_dailybasic_df = get_index_dailybasic_with_retry(today, max_retries, wait_seconds)
    if index_dailybasic_df is None:
        logger.error(f"无法获取完整的指数每日指标数据，请检查数据源")
        return
    
    if not save_to_database(
        df=index_dailybasic_df,
        table_name='a_stock_index_dailybasic',
        conflict_columns=['index_code', 'trade_date'],
        update_columns=['total_mv','float_mv','total_share','float_share','free_share','turnover_rate','turnover_rate_f','pe','pe_ttm','pb','index_name'],
        data_type='指数每日指标',
        engine=engine
    ):
        return
        
    # 计算指数市值得分
    logger.info(f"开始计算最近{n_days}天的指数市值得分...")
    index_score = calculate_index_marketvalue_score(index_dailybasic_df)
    if index_score is None:
        logger.error(f"无法获取完整的指数市值得分，请检查数据源")
        return

    if not save_to_database(
        df=index_score,
        table_name='a_stock_index_marketvalue_score',
        conflict_columns=['index_code', 'trade_date'],
        update_columns=['index_name','流通市值(亿元)','流通市值分位数','换手率(%)','换手率分位数','平均价格'],
        data_type='指数市值得分',
        engine=engine
    ):
        return


    # 计算个股资金流向得分
    logger.info(f"开始计算最近{n_days}天的个股资金流向得分...")
    stock_rank = calculate_stock_score(moneyflow_df, basic_df, daily_k_df)
    if stock_rank is None:
        logger.error(f"无法获取完整的个股资金流向得分，请检查数据源")
        return
    
    if not save_to_database(
        df=stock_rank,
        table_name='a_stock_moneyflow_score',
        conflict_columns=['ts_code', 'trade_date'],
        data_type='个股资金流向得分',
        update_columns=['统计天数','排名','市值区间','特大单/市值','特大单/市值_Z','大单/市值','大单/市值_Z','中单/市值','中单/市值_Z','小单/市值','小单/市值_Z','换手率均值','换手率均值_Z','量比均值','量比均值_Z','成交额分位数','净流入分位数','综合得分'],
        engine=engine
    ):
        return

    # 计算行业资金流向得分
    logger.info(f"开始计算最近{n_days}天的行业资金流向得分...")
    industry_rank = calculate_industry_score(industry_moneyflow_df)
    if industry_rank is None:
        logger.error(f"无法获取完整的行业资金流向得分，请检查数据源")
        return
    
    if not save_to_database(
        df=industry_rank,
        table_name='a_stock_moneyflow_industry_score',
        conflict_columns=['industry_code', 'trade_date'],
        update_columns=['industry','排名','净额(亿元)','净额分位数','过去5日分位数','过去4日分位数','过去3日分位数','过去2日分位数','过去1日分位数'],
        data_type='行业资金流向得分',
        engine=engine
    ):
        return

    # 计算市场资金净流入分位数
    logger.info(f"开始计算最近{n_days}天的市场资金净流入分位数...")
    market_score = calculate_market_moneyflow_score(industry_moneyflow_df)
    if market_score is None:
        logger.error(f"无法获取完整的行业资金流向得分，请检查数据源")
        return
    
    if not save_to_database(
        df=market_score,
        table_name='a_stock_market_moneyflow_score',
        conflict_columns=['trade_date'],
        update_columns=['净值(亿元)','净值分位数'],
        data_type='市场资金净流入分位数',
        engine=engine
    ):
        return

    logger.info(f"{today}的任务完成!!!")
    send_notification_wecom(f"{today}的daily task完成!!!", f"{today}的daily task完成!!!")

def main():
    """主函数"""
    schedule.every().day.at(run_time).do(daily_task)
    
    logger.info(f"定时任务已启动，将在每个交易日下午{run_time}执行...")
    while True:
        schedule.run_pending()
        time.sleep(sleep_seconds)

if __name__ == "__main__":
    main()



