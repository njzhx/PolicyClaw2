import time
import sys
from datetime import datetime
from io import StringIO

# 导入飞书通知模块
try:
    from feishu_notifier import send_crawler_result
except ImportError:
    send_crawler_result = None


class DualOutput:
    """双输出流，同时输出到控制台和缓冲区"""
    
    def __init__(self, original_stdout):
        self.original_stdout = original_stdout
        self.buffer = StringIO()
    
    def write(self, text):
        self.original_stdout.write(text)
        self.buffer.write(text)
    
    def flush(self):
        self.original_stdout.flush()
        self.buffer.flush()
    
    def getvalue(self):
        return self.buffer.getvalue()


# ==========================================
# 爬虫管理系统
# 功能：执行多个爬虫，一个爬虫出错不影响其他爬虫
# ==========================================

class CrawlerManager:
    def __init__(self):
        """初始化爬虫管理器"""
        self.crawlers = []
        self.results = {}
    
    def register_crawler(self, name, crawler_func, crawler_module):
        """注册爬虫
        
        Args:
            name: 爬虫名称
            crawler_func: 爬虫执行函数
            crawler_module: 爬虫模块对象，用于获取 TARGET_URL
        """
        target_url = getattr(crawler_module, 'TARGET_URL', '')
        self.crawlers.append((name, crawler_func, target_url))
        if target_url:
            print(f"✅ 已注册爬虫: {name} ({target_url})")
        else:
            print(f"✅ 已注册爬虫: {name}")
    
    def run_all_crawlers(self):
        """执行所有爬虫
        
        Returns:
            dict: 各爬虫执行结果
        """
        # 开始捕获输出
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        dual_out = DualOutput(original_stdout)
        dual_err = DualOutput(original_stderr)
        sys.stdout = dual_out
        sys.stderr = dual_err
        
        start_datetime = datetime.now()
        print(f"\n🚀 开始执行爬虫任务 - {start_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)
        
        total_start_time = time.time()
        
        for name, crawler_func, target_url in self.crawlers:
            if target_url:
                print(f"\n📦 开始执行爬虫: {name}")
                print(f"🔗 目标网址: {target_url}")

            else:
                print(f"\n📦 开始执行爬虫: {name}")
            print("-" * 40)
            
            start_time = time.time()
            
            try:
                # 创建临时输出缓冲区，用于捕获当前爬虫的输出
                from io import StringIO
                temp_stdout = StringIO()
                temp_stderr = StringIO()
                original_stdout = sys.stdout
                original_stderr = sys.stderr
                sys.stdout = temp_stdout
                sys.stderr = temp_stderr
                
                # 执行爬虫
                result = crawler_func()
                
                # 捕获当前爬虫的输出
                crawler_output = temp_stdout.getvalue() + temp_stderr.getvalue()
                
                # 恢复标准输出
                sys.stdout = original_stdout
                sys.stderr = original_stderr
                
                # 将捕获的输出写回原始输出流，保持原有输出显示
                print(crawler_output, end='')
                
                # 记录结果
                execution_time = time.time() - start_time
                
                # 区分抓取数量和写入数量
                # 处理可能的元组返回值（包含API推送结果）
                crawl_count = 0
                write_count = 0
                filter_count = 0
                api_push_result = None
                
                if isinstance(result, tuple) and len(result) == 2:
                    data_list, api_push_result = result
                    crawl_count = len(data_list)
                    write_count = len(data_list)
                else:
                    data_list = result
                    crawl_count = len(data_list)
                    write_count = len(data_list)
                
                # 尝试从当前爬虫的输出中提取过滤数量
                import re
                # 匹配多种过滤信息格式
                filter_match = re.search(r'(?:过滤掉|过滤非昨日数据|过滤掉非目标日期数据)\s*[:：]?\s*(\d+)\s*条', crawler_output)
                if filter_match:
                    filter_count = int(filter_match.group(1))
                
                self.results[name] = {
                    'status': 'success',
                    'crawl_count': crawl_count,
                    'write_count': write_count,
                    'filter_count': filter_count,
                    'execution_time': round(execution_time, 2),
                    'timestamp': datetime.now().isoformat(),
                    'target_url': target_url,
                    'api_push_result': api_push_result
                }
                
                print(f"✅ 爬虫 {name} 执行成功")
                print(f"📊 抓取数据: {crawl_count} 条")
                print(f"💾 写入数据库: {crawl_count} 条")
                print(f"⏱️  执行时间: {round(execution_time, 2)} 秒")
                
            except Exception as e:
                # 捕获异常，确保其他爬虫继续执行
                execution_time = time.time() - start_time
                self.results[name] = {
                    'status': 'error',
                    'crawl_count': 0,
                    'write_count': 0,
                    'error_message': str(e),
                    'execution_time': round(execution_time, 2),
                    'timestamp': datetime.now().isoformat(),
                    'target_url': target_url
                }
                
                print(f"❌ 爬虫 {name} 执行失败")
                print(f"💥 错误信息: {str(e)}")
                print(f"📊 抓取数据: 0 条")
                print(f"💾 写入数据库: 0 条")
                print(f"⏱️  执行时间: {round(execution_time, 2)} 秒")
            
            print("-" * 40)
        
        total_execution_time = time.time() - total_start_time
        end_datetime = datetime.now()
        
        print("=" * 60)
        print(f"📋 爬虫执行完成 - {end_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"⏱️  总执行时间: {round(total_execution_time, 2)} 秒")
        print(f"📦 执行爬虫数: {len(self.crawlers)}")
        
        # 统计结果
        success_count = sum(1 for r in self.results.values() if r['status'] == 'success')
        error_count = sum(1 for r in self.results.values() if r['status'] == 'error')
        
        # 统计总抓取和写入数量
        total_crawl = sum(r.get('crawl_count', 0) for r in self.results.values())
        total_write = sum(r.get('write_count', 0) for r in self.results.values())
        
        print(f"✅ 成功: {success_count} 个")
        print(f"❌ 失败: {error_count} 个")
        print(f"📊 总抓取数据: {total_crawl} 条")
        print(f"💾 总写入数据库: {total_write} 条")
        
        # 获取完整日志
        full_log = dual_out.getvalue() + dual_err.getvalue()
        
        # 从日志中解析API推送结果
        import re
        api_results = {}
        api_success_count = 0
        api_error_count = 0
        
        # 匹配"✅ {crawler_name}：成功推送 X 条数据到API"格式
        api_pattern = re.compile(r'✅\s*([^：\n]+)：成功推送\s*(\d+)\s*条数据到API')
        for match in api_pattern.finditer(full_log):
            crawler_name = match.group(1).strip()
            count = match.group(2)
            message = f"成功推送 {count} 条数据到API"
            api_results[crawler_name] = {"status": "success", "message": message}
            api_success_count += 1
        
        # 匹配"❌ {crawler_name}：API推送失败 - ..."格式
        api_error_pattern = re.compile(r'❌\s*([^：\n]+)：API推送失败\s*-\s*(.*?)(?=\n|$)')
        for match in api_error_pattern.finditer(full_log):
            crawler_name = match.group(1).strip()
            error_msg = match.group(2).strip()
            message = f"API推送失败 - {error_msg}"
            api_results[crawler_name] = {"status": "error", "message": message}
            api_error_count += 1
        
        # 将解析到的API推送结果保存到self.results中
        for crawler_name, result in self.results.items():
            if crawler_name in api_results:
                result['api_push_result'] = api_results[crawler_name]
        
        # 恢复标准输出
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        
        # 输出API推送结果
        print("\n📡 API推送结果:")
        print("-" * 40)
        
        if api_results:
            for crawler_name, api_result in api_results.items():
                if api_result.get('status') == 'success':
                    print(f"✅ {crawler_name}：{api_result.get('message')}")
                else:
                    print(f"❌ {crawler_name}：{api_result.get('message')}")
            print(f"📊 API推送统计: 成功 {api_success_count} 个, 失败 {api_error_count} 个")
        else:
            print("⚠️  没有API推送记录")
        print("-" * 40)
        
        # 推送每日状态数据到API
        try:
            from db_utils import push_daily_status
            date_str = start_datetime.date().isoformat()
            daily_success_count = total_crawl  # 使用总抓取数量作为成功数
            daily_fail_count = error_count  # 使用失败的爬虫数作为失败数
            print("\n📅 推送每日状态数据...")
            daily_status_result = push_daily_status(date_str, daily_success_count, daily_fail_count)
            if isinstance(daily_status_result, dict):
                status = daily_status_result.get('status', 'unknown')
                message = daily_status_result.get('message', '')
                if status == 'success':
                    print(f"✅ 每日状态数据推送成功：{message}")
                else:
                    print(f"❌ 每日状态数据推送失败：{message}")
        except Exception as e:
            print(f"⚠️  推送每日状态数据时发生错误：{e}")
        
        # 发送飞书通知
        if send_crawler_result:
            print("\n📤 正在发送飞书通知...")
            send_crawler_result(self.results, start_datetime, end_datetime, full_log)
        
        return self.results
    
    def get_summary(self):
        """获取执行摘要"""
        if not self.results:
            return "尚未执行爬虫任务"
        
        summary = []
        for name, result in self.results.items():
            if result['status'] == 'success':
                summary.append(f"✅ {name}: 抓取 {result['crawl_count']} 条，写入数据库 {result['write_count']} 条")
            else:
                summary.append(f"❌ {name}: 执行失败 - {result['error_message'][:100]}...")
        
        return "\n".join(summary)

# ==========================================
# 主执行逻辑
# ==========================================
if __name__ == "__main__":
    # 创建爬虫管理器
    manager = CrawlerManager()
    
    # 注册爬虫
    # 注意：这里需要根据实际爬虫模块进行导入和注册
    
    # 导入中国政府网爬虫
    try:
        from Ministries import gov_crawler
        manager.register_crawler("中国政府网", gov_crawler.run, gov_crawler)
    except ImportError as e:
        print(f"⚠️  导入中国政府网爬虫失败: {e}")
    
    # 导入中国政府网政策解读爬虫
    try:
        from Ministries import gov_interpretation_crawler
        manager.register_crawler("中国政府网政策解读", gov_interpretation_crawler.run, gov_interpretation_crawler)
    except ImportError as e:
        print(f"⚠️  导入中国政府网政策解读爬虫失败: {e}")
    
    # 导入国家发改委爬虫
    try:
        from Ministries import ndrc_crawler
        manager.register_crawler("国家发改委", ndrc_crawler.run, ndrc_crawler)
    except ImportError as e:
        print(f"⚠️  导入国家发改委爬虫失败: {e}")
    
    # 导入人民网财经爬虫
    # try:
    #     from Ministries import people_finance_crawler
    #     manager.register_crawler("人民网财经", people_finance_crawler.run, people_finance_crawler)
    # except ImportError as e:
    #     print(f"⚠️  导入人民网财经爬虫失败: {e}")
    
    # 注册 mubiao.md 中的16个新爬虫
    try:
        from Ministries import miit_wjk_crawler
        manager.register_crawler("工信部_文件库", miit_wjk_crawler.run, miit_wjk_crawler)
    except ImportError as e:
        print(f"⚠️  导入工信部_文件库爬虫失败: {e}")
    
    try:
        from Ministries import miit_zcjd_crawler
        manager.register_crawler("工信部_政策解读", miit_zcjd_crawler.run, miit_zcjd_crawler)
    except ImportError as e:
        print(f"⚠️  导入工信部_政策解读爬虫失败: {e}")
    
    try:
        from Ministries import nda_zwgk_crawler
        manager.register_crawler("数据局_政务公开", nda_zwgk_crawler.run, nda_zwgk_crawler)
    except ImportError as e:
        print(f"⚠️  导入数据局_政务公开爬虫失败: {e}")
    
    try:
        from Ministries import mohurd_wjk_crawler
        manager.register_crawler("住建部_文件库", mohurd_wjk_crawler.run, mohurd_wjk_crawler)
    except ImportError as e:
        print(f"⚠️  导入住建部_文件库爬虫失败: {e}")
    
    try:
        from Jiangsu import jiangsu_gov_zxwj_crawler
        manager.register_crawler("省政府_最新文件", jiangsu_gov_zxwj_crawler.run, jiangsu_gov_zxwj_crawler)
    except ImportError as e:
        print(f"⚠️  导入省政府_最新文件爬虫失败: {e}")
    
    try:
        from Jiangsu import jiangsu_gov_zcjd_crawler
        manager.register_crawler("省政府_政策解读", jiangsu_gov_zcjd_crawler.run, jiangsu_gov_zcjd_crawler)
    except ImportError as e:
        print(f"⚠️  导入省政府_政策解读爬虫失败: {e}")
    
    try:
        from Jiangsu import jiangsu_gov_gb_crawler
        manager.register_crawler("省政府_省政府公报", jiangsu_gov_gb_crawler.run, jiangsu_gov_gb_crawler)
    except ImportError as e:
        print(f"⚠️  导入省政府_省政府公报爬虫失败: {e}")
    
    try:
        from Jiangsu import jiangsu_fzggw_zcwj_crawler
        manager.register_crawler("省发改委_政策文件", jiangsu_fzggw_zcwj_crawler.run, jiangsu_fzggw_zcwj_crawler)
    except ImportError as e:
        print(f"⚠️  导入省发改委_政策文件爬虫失败: {e}")
    
    try:
        from Jiangsu import jiangsu_fzggw_zcjd_crawler
        manager.register_crawler("省发改委_政策解读", jiangsu_fzggw_zcjd_crawler.run, jiangsu_fzggw_zcjd_crawler)
    except ImportError as e:
        print(f"⚠️  导入省发改委_政策解读爬虫失败: {e}")
    
    try:
        from Jiangsu import jiangsu_fzggw_tzgg_crawler
        manager.register_crawler("省发改委_通知公告", jiangsu_fzggw_tzgg_crawler.run, jiangsu_fzggw_tzgg_crawler)
    except ImportError as e:
        print(f"⚠️  导入省发改委_通知公告爬虫失败: {e}")
    
    try:
        from Jiangsu import jiangsu_gxt_gsgg_crawler
        manager.register_crawler("省工信厅_公示公告", jiangsu_gxt_gsgg_crawler.run, jiangsu_gxt_gsgg_crawler)
    except ImportError as e:
        print(f"⚠️  导入省工信厅_公示公告爬虫失败: {e}")
    
    try:
        from Jiangsu import jiangsu_gxt_wjtz_crawler
        manager.register_crawler("省工信厅_文件通知", jiangsu_gxt_wjtz_crawler.run, jiangsu_gxt_wjtz_crawler)
    except ImportError as e:
        print(f"⚠️  导入省工信厅_文件通知爬虫失败: {e}")
    
    try:
        from Jiangsu import jiangsu_gxt_zcwj_crawler
        manager.register_crawler("省工信厅_政策文件", jiangsu_gxt_zcwj_crawler.run, jiangsu_gxt_zcwj_crawler)
    except ImportError as e:
        print(f"⚠️  导入省工信厅_政策文件爬虫失败: {e}")
    
    try:
        from Jiangsu import jiangsu_sjj_zcfb_crawler
        manager.register_crawler("省数据局_政策发布", jiangsu_sjj_zcfb_crawler.run, jiangsu_sjj_zcfb_crawler)
    except ImportError as e:
        print(f"⚠️  导入省数据局_政策发布爬虫失败: {e}")
    
    try:
        from Jiangsu import jiangsu_sjj_zcjd_crawler
        manager.register_crawler("省数据局_政策解读", jiangsu_sjj_zcjd_crawler.run, jiangsu_sjj_zcjd_crawler)
    except ImportError as e:
        print(f"⚠️  导入省数据局_政策解读爬虫失败: {e}")
    
    try:
        from Jiangsu import jiangsu_czt_gg_crawler
        manager.register_crawler("财政厅_公告", jiangsu_czt_gg_crawler.run, jiangsu_czt_gg_crawler)
    except ImportError as e:
        print(f"⚠️  导入财政厅_公告爬虫失败: {e}")
    
    try:
        from Jiangsu import jiangsu_sjj_gg_crawler
        manager.register_crawler("省数据局_通知公告", jiangsu_sjj_gg_crawler.run, jiangsu_sjj_gg_crawler)
    except ImportError as e:
        print(f"⚠️  导入省数据局_通知公告爬虫失败: {e}")
    
    try:
        from Ministries import miit_wjfb_crawler
        manager.register_crawler("工信部_文件发布", miit_wjfb_crawler.run, miit_wjfb_crawler)
    except ImportError as e:
        print(f"⚠️  导入工信部_文件发布爬虫失败: {e}")
    
    try:
        from Ministries import miit_gzdt_crawler
        manager.register_crawler("工信部_工作动态", miit_gzdt_crawler.run, miit_gzdt_crawler)
    except ImportError as e:
        print(f"⚠️  导入工信部_工作动态爬虫失败: {e}")
    
    # 导入工信部网站tabbox爬虫
    try:
        from Ministries import miit_tabbox_crawler
        manager.register_crawler("工信部_网站tabbox", miit_tabbox_crawler.run, miit_tabbox_crawler)
    except ImportError as e:
        print(f"⚠️  导入工信部_网站tabbox爬虫失败: {e}")
    
    # 导入江苏省住房和城乡建设厅爬虫
    try:
        from Jiangsu import jiangsu_zfhcxjst_tf_crawler
        manager.register_crawler("江苏省住房和城乡建设厅", jiangsu_zfhcxjst_tf_crawler.run, jiangsu_zfhcxjst_tf_crawler)
    except ImportError as e:
        print(f"⚠️  导入江苏省住房和城乡建设厅爬虫失败: {e}")
    
    # 导入江苏省商务厅意见征集爬虫
    try:
        from Jiangsu import jiangsu_swt_yjzj_crawler
        manager.register_crawler("江苏省商务厅_意见征集", jiangsu_swt_yjzj_crawler.run, jiangsu_swt_yjzj_crawler)
    except ImportError as e:
        print(f"⚠️  导入江苏省商务厅_意见征集爬虫失败: {e}")
    
    # 导入江苏省商务厅公告通知爬虫
    try:
        from Jiangsu import jiangsu_swt_ggtz_crawler
        manager.register_crawler("江苏省商务厅_公告通知", jiangsu_swt_ggtz_crawler.run, jiangsu_swt_ggtz_crawler)
    except ImportError as e:
        print(f"⚠️  导入江苏省商务厅_公告通知爬虫失败: {e}")
    
    # 导入江苏省商务厅政策及公告爬虫
    try:
        from Jiangsu import jiangsu_swt_zcgg_crawler
        manager.register_crawler("江苏省商务厅_政策及公告", jiangsu_swt_zcgg_crawler.run, jiangsu_swt_zcgg_crawler)
    except ImportError as e:
        print(f"⚠️  导入江苏省商务厅_政策及公告爬虫失败: {e}")
    
    # 导入商务部政策发布爬虫
    try:
        from Ministries import mofcom_zcfb_crawler
        manager.register_crawler("商务部_政策发布", mofcom_zcfb_crawler.run, mofcom_zcfb_crawler)
    except ImportError as e:
        print(f"⚠️  导入商务部_政策发布爬虫失败: {e}")
    
    # 导入商务部工作通知爬虫
    try:
        from Ministries import mofcom_gztz_crawler
        manager.register_crawler("商务部_工作通知", mofcom_gztz_crawler.run, mofcom_gztz_crawler)
    except ImportError as e:
        print(f"⚠️  导入商务部_工作通知爬虫失败: {e}")
    
    # 导入商务部规划计划爬虫
    try:
        from Ministries import mofcom_ghjh_crawler
        manager.register_crawler("商务部_规划计划", mofcom_ghjh_crawler.run, mofcom_ghjh_crawler)
    except ImportError as e:
        print(f"⚠️  导入商务部_规划计划爬虫失败: {e}")
    
    # 导入江苏省农业农村厅通知公告爬虫
    try:
        from Jiangsu import jiangsu_agriculture_crawler
        manager.register_crawler("江苏省农业农村厅_通知公告", jiangsu_agriculture_crawler.run, jiangsu_agriculture_crawler)
    except ImportError as e:
        print(f"⚠️  导入江苏省农业农村厅_通知公告爬虫失败: {e}")
    
    # 导入江苏省教育厅政策文件爬虫
    try:
        from Jiangsu import jiangsu_jyt_zcwj_crawler
        manager.register_crawler("江苏省教育厅_政策文件", jiangsu_jyt_zcwj_crawler.run, jiangsu_jyt_zcwj_crawler)
    except ImportError as e:
        print(f"⚠️  导入江苏省教育厅_政策文件爬虫失败: {e}")

    # 导入江苏省科学技术厅政策文件爬虫
    try:
        from Jiangsu import jiangsu_kxjst_zcwj_crawler
        manager.register_crawler("江苏省科学技术厅_政策文件", jiangsu_kxjst_zcwj_crawler.run, jiangsu_kxjst_zcwj_crawler)
    except ImportError as e:
        print(f"⚠️  导入江苏省科学技术厅_政策文件爬虫失败: {e}")

    # 导入江苏省知产局通知公告爬虫
    try:
        from Jiangsu import jiangsu_zhichanju_tzgg_crawler
        manager.register_crawler("江苏省知识产权局_通知公告", jiangsu_zhichanju_tzgg_crawler.run, jiangsu_zhichanju_tzgg_crawler)
    except ImportError as e:
        print(f"⚠️  导入江苏省知识产权局_通知公告爬虫失败: {e}")

     # 导入江苏省国资委政策文件爬虫
    try:
        from Jiangsu import jiangsu_gzw_crawler
        manager.register_crawler("江苏省国资委_政策文件", jiangsu_gzw_crawler.run, jiangsu_gzw_crawler)
    except ImportError as e:
        print(f"⚠️  导入江苏省国资委_政策文件爬虫失败: {e}")

    # 导入江苏省市场监管局政策文件爬虫
    try:
        from Jiangsu import jiangsu_scjgj_zcwj_crawler
        manager.register_crawler("江苏省市场监管局_政策文件", jiangsu_scjgj_zcwj_crawler.run, jiangsu_scjgj_zcwj_crawler)
    except ImportError as e:
        print(f"⚠️  导入江苏省市场监管局_政策文件爬虫失败: {e}")

     # 导入江苏省交通运输厅政策文件爬虫
    try:
        from Jiangsu import jiangsu_jtyst_zcwj_crawler
        manager.register_crawler("江苏省交通运输厅_政策文件", jiangsu_jtyst_zcwj_crawler.run, jiangsu_jtyst_zcwj_crawler)
    except ImportError as e:
        print(f"⚠️  导入江苏省交通运输厅_政策文件爬虫失败: {e}")

    # 导入江苏省应急管理厅通知公告爬虫
    try:
        from Jiangsu import jiangsu_yjglt_tzgg_crawler
        manager.register_crawler("江苏省应急管理厅_通知公告", jiangsu_yjglt_tzgg_crawler.run, jiangsu_yjglt_tzgg_crawler)
    except ImportError as e:
        print(f"⚠️  导入江苏省应急管理厅_通知公告爬虫失败: {e}")
    
    # 导入江苏省自然资源厅政策文件爬虫
    try:
        from Jiangsu import jiangsu_zrzy_crawler
        manager.register_crawler("江苏省自然资源厅_政策文件", jiangsu_zrzy_crawler.run, jiangsu_zrzy_crawler)
    except ImportError as e:
        print(f"⚠️  导入江苏省自然资源厅政策文件爬虫失败: {e}")

    # 导入江苏省民宗委通知公告爬虫
    try:
        from Jiangsu import jiangsu_mzw_tzgg_crawler
        manager.register_crawler("江苏省民宗委_通知公告", jiangsu_mzw_tzgg_crawler.run, jiangsu_mzw_tzgg_crawler)
    except ImportError as e:
        print(f"⚠️  导入江苏省民宗委通知公告爬虫失败: {e}")

    # 导入江苏省公安厅政策文件爬虫
    try:
        from Jiangsu import jiangsu_gat_zcwj_crawler
        manager.register_crawler("江苏省公安厅_政策文件", jiangsu_gat_zcwj_crawler.run, jiangsu_gat_zcwj_crawler)
    except ImportError as e:
        print(f"⚠️  导入江苏省公安厅政策文件爬虫失败: {e}")

    # 导入江苏省民政厅政策文件爬虫
    try:
        from Jiangsu import jiangsu_mzt_zcwj_crawler
        manager.register_crawler("江苏省民政厅_政策文件", jiangsu_mzt_zcwj_crawler.run, jiangsu_mzt_zcwj_crawler)
    except ImportError as e:
        print(f"⚠️  导入江苏省民政厅政策文件爬虫失败: {e}")

    # 导入江苏省人社厅重大民生信息爬虫
    try:
        from Jiangsu import jiangsu_jshrss_zdgkc_crawler
        manager.register_crawler("江苏省人社厅_重大民生信息", jiangsu_jshrss_zdgkc_crawler.run, jiangsu_jshrss_zdgkc_crawler)
    except ImportError as e:
        print(f"⚠️  导入江苏省人社厅重大民生信息爬虫失败: {e}")

    # 导入江苏省财政厅政策发布爬虫
    try:
        from Jiangsu import jiangsu_czt_zcgg_crawler
        manager.register_crawler("江苏省财政厅_政策发布", jiangsu_czt_zcgg_crawler.run, jiangsu_czt_zcgg_crawler)
    except ImportError as e:
        print(f"⚠️  导入江苏省财政厅政策发布爬虫失败: {e}")

    # 导入江苏省生态环境厅通知爬虫
    try:
        import jiangsu_sthjt_tzgg_crawler
        manager.register_crawler("江苏省生态环境厅_通知", jiangsu_sthjt_tzgg_crawler.run, jiangsu_sthjt_tzgg_crawler)
    except ImportError as e:
        print(f"⚠️  导入江苏省生态环境厅通知爬虫失败: {e}")

    # 导入江苏省卫健委规范性文件爬虫
    try:
        from Jiangsu import jiangsu_wjw_zcwj_crawler
        manager.register_crawler("江苏省卫健委_规范性文件", jiangsu_wjw_zcwj_crawler.run, jiangsu_wjw_zcwj_crawler)
    except ImportError as e:
        print(f"⚠️  导入江苏省卫健委规范性文件爬虫失败: {e}")

    # 导入江苏省应急管理厅通知公告爬虫
    try:
        from Jiangsu import jiangsu_yjglt_tzgg_crawler
        manager.register_crawler("江苏省应急管理厅_通知公告", jiangsu_yjglt_tzgg_crawler.run, jiangsu_yjglt_tzgg_crawler)
    except ImportError as e:
        print(f"⚠️  导入江苏省应急管理厅通知公告爬虫失败: {e}")

    # 导入江苏省水利厅规范性文件爬虫
    try:
        from Jiangsu import jiangsu_jswater_zcwj_crawler
        manager.register_crawler("江苏省水利厅_规范性文件", jiangsu_jswater_zcwj_crawler.run, jiangsu_jswater_zcwj_crawler)
    except ImportError as e:
        print(f"⚠️  导入江苏省水利厅规范性文件爬虫失败: {e}")
        
    # 执行所有爬虫
    if manager.crawlers:
        results = manager.run_all_crawlers()
        
        # 打印执行摘要
        print("\n📊 执行摘要:")
        print("=" * 60)
        print(manager.get_summary())
    else:
        print("⚠️  没有注册任何爬虫")
