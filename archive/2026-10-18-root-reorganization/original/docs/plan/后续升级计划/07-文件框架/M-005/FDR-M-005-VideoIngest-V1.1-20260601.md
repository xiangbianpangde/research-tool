# 框架决策记录 — M-005 转写器（DD-M-005）

> **生成方**：DD-M-005
> **日期**：2026-06-01
> **决策数**：3（草稿→已接受）

---

## FDR-001 单文件 transcriber.py 组织方式

```
[决策编号] FDR-001
[决策标题] M-005 采用单文件 transcriber.py 组织
[决策状态] 已接受
[决策内容] 将 6 个类（EngineSelector/WhisperEngine/BcutEngine/GroqEngine/ModelManager/CEREstimator）+ 5 个公开函数 + 1 个模板方法集中在 research_tool/transcriber.py 单文件中
[决策理由]
  理由1: 严格遵循 DD-001:FS-NNN 规定的「单文件 transcriber.py」规范
  理由2: M-001 CLI 入口直接 import 单一模块，调用路径清晰
  理由3: DD-S 骨架搭建工作量更小（一次骨架覆盖 12 个 API）
  理由4: 单文件函数数 11 < 20 上限（[soul §4.2 单文件函数数上限]）
  理由5: V1.1 阶段三引擎可插拔性由策略模式保证，物理拆分收益不显著
[拒绝的替代方案]
  替代方案A: 子包化 transcriber/{engines,selector,model_manager,cer,...}.py
    拒绝理由: 违反 FS-NNN 单文件规范；多实例隔离协议要求模块前缀；M-001 需修改 import 路径；DD-S 协调成本增加（4.11 对比得分 6.5 vs 9.2）
[影响范围]
  影响文件: research_tool/transcriber.py（创建）
  影响模块: M-005 自身 + 间接 M-001/M-004/M-007（依赖 M-005）
  影响接口: API-012（无变化）
[相关FDR] FDR-002（设计模式）、FDR-003（测试组织）
[来源标注] [DD-001:FS-NNN] [DD-001:MD-M-005] [DD-M推断:依据 — 4.11 多方案对比 差值 2.7]
```

---

## FDR-002 策略模式 + 模板方法 双设计模式

```
[决策编号] FDR-002
[决策标题] M-005 采用策略模式（引擎可插拔）+ 模板方法（流程骨架）双模式
[决策状态] 已接受
[决策内容] 
  策略模式: EngineSelector 选择主引擎 + WhisperEngine/BcutEngine/GroqEngine 三个策略类实现统一 transcribe(audio_path) 接口
  模板方法: run_transcription_pipeline() 定义主流程骨架（detect_ram → select → transcribe → fallback），子步骤由各策略类实现
[决策理由]
  理由1: DD-001:MD-M-005 明确推荐此双模式（[AR:DP-005]）
  理由2: 策略模式保证引擎可插拔性（V2.0 可加新引擎如阿里达摩院）
  理由3: 模板方法集中 fallback 链逻辑，避免在每个引擎类中重复
  理由4: 与 [调研:S-004] 推荐的 faster-whisper 调度模式一致
[拒绝的替代方案]
  替代方案A: 纯策略模式（无模板方法）
    拒绝理由: fallback 链逻辑将分散到 3 个引擎类，难以统一管理
  替代方案B: 责任链模式
    拒绝理由: 与策略模式相比，责任链在 V1.1 三引擎场景下无明显优势；增加抽象层级
  替代方案C: 单一引擎硬编码
    拒绝理由: 不满足 [DD-001:IC-012 三引擎调度] 要求
[影响范围]
  影响文件: research_tool/transcriber.py（策略类 + 模板方法）
  影响接口: API-012（不变；内部结构变化）
  影响测试: TestEngineSelector + TestTranscribeIntegration
[相关FDR] FDR-001（单文件组织）
[来源标注] [DD-001:MD-M-005 设计模式] [调研:S-004] [AR:DP-005]
```

---

## FDR-003 ctranslate2 锁的并发处理策略

```
[决策编号] FDR-003
[决策标题] M-005 ctranslate2 互斥锁采用「单实例 + Semaphore(3) 串行化」处理
[决策状态] 已接受
[决策内容] faster-whisper 走 ctranslate2 时存在全局互斥锁，框架约定：
  规则1: 模块内单实例 WhisperModel（避免多实例触发锁冲突）
  规则2: 跨任务并发经 M-012 Semaphore(3) 串行化（不破坏模块内单例）
  规则3: 长时间任务主动调用 WhisperEngine.unload_model() 释放内存
[决策理由]
  理由1: [AR:洞察#3 ctranslate2 锁] 明确指出多实例会触发全局锁
  理由2: 调研 [调研:S-004] 推荐单实例 + 任务级串行化模式
  理由3: 与 M-012 Semaphore(3) 现有架构兼容（[DD-001:IC-029]）
  理由4: 测试用例 TestConcurrency 验证并发场景下锁不破坏数据一致性
[拒绝的替代方案]
  替代方案A: 每任务一个 WhisperModel 实例
    拒绝理由: 触发 ctranslate2 锁；高内存占用；OOM 风险
  替代方案B: 多进程池（multiprocessing）
    拒绝理由: 跨进程共享音频文件 + 模型文件路径复杂；增加部署成本；V1.1 不必要
  替代方案C: 移除锁检测，依赖 OS 调度
    拒绝理由: 不可靠；偶发死锁；用户感知差
[影响范围]
  影响文件: research_tool/transcriber.py（WhisperEngine 单例约束）
  影响模块: M-012（Semaphore 编排）+ M-001（任务生命周期）
  影响测试: TestConcurrency（2 用例覆盖锁场景）
  风险登记: 若未来 V2.0 引入多 GPU 并行，需重新设计（V1.1 不在范围）
[相关FDR] FDR-002（双设计模式 - 模板方法约束调用顺序）
[来源标注] [AR:洞察#3 ctranslate2 锁] [调研:S-004] [DD-001:IC-029]
```

---

## 决策汇总

| FDR | 决策 | 状态 | 影响范围 |
|-----|------|------|---------|
| FDR-001 | 单文件 transcriber.py 组织 | 已接受 | M-005 主文件 |
| FDR-002 | 策略模式 + 模板方法 | 已接受 | M-005 类设计 |
| FDR-003 | ctranslate2 锁 单实例 + Semaphore(3) 串行化 | 已接受 | M-005 + M-012 |

**3/3 决策均已接受。** 全部基于 DD-001 规范 + 4.11 多方案对比 + AR 调研/洞察支持。

---

> **本文件结束**。M-005 框架决策记录就绪，FDR 覆盖率 100%。
