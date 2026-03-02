# 迁移文件历史记录

- 初始化：[2be9eec66e37](./versions/2be9eec66e37_init.py)
- 给苗情监控的表`job`添加了两个字段：[d6fafbb6e061-2be9eec66e37](./versions/d6fafbb6e061_job_table_add_column_score_level.py)
- 给`job`添加字段`status`:[0b1857aae626-d6fafbb6e061](./versions/0b1857aae626_job_table_add_column_score_level_status.py)
- 添加 `odm` 表: [194b97582287-0b1857aae626](./versions/194b97582287_create_odm_table.py)
- 添加 `odm_generate_task` 表: [605c0e2dea9f-194b97582287](./versions/605c0e2dea9f_create_odm_generate_task_table.py)
- 添加 三维重建的表：[5f07e8cb2616-605c0e2dea9f](./versions/5f07e8cb2616_create_3dcm_table.py)
- 添加ODM植被指数报告表：[81d8e7772333-5f07e8cb2616](./versions/81d8e7772333_adjusted_odm_report_tables.py)
- 添加ODM采样统计表：[750d468cc4bb-81d8e7772333](./versions/750d468cc4bb_add_quadrat_statistics_table.py)