研究区域指定，然后构建3d场景。

# todo
- [ ] todo
- [ ] metrics
- [ ] LLM评价，当前只有LLM生成plan
- [ ] 场景图叠加到3D场景中展示。
- [ ] png图+json文件的前后对比。
- [ ] 十个预设的场景结果
- [ ] 3d场景前后对比


---

### 1. 输入部分 (Input Section)

该阶段为系统提供原始数据和设计约束：

- **城市背景 (Urban Context)：** 包含街道平面图（Street Plan）和兴趣点（POI）数据。
    
- **设计手册 (Design Handbook)：** 提供平面约束（Plan constraint）和设施约束（Furniture Constraint）。
    
- **外部输入：** 接收用户提示词（Prompt），所有信息共同输入至核心 **LLM**。
    

### 2. 街道布局生成 (Street Layout Generation)

LLM 根据输入生成街道的逻辑结构与物体配置：

- **图层生成 (Layers Generation)：** 生成有效区域（Valid Region）、车行道（Drive Lane）、主人行道（Main Sidewalk）及近路设计（Near-road Furnishing）。
    
- **物体放置 (Object Placement)：** 确定城市家具（Urban Furniture）的布局。该过程支持“人机回环”（Human in the loop）干预或由 LLM 随机生成（LLM Randomly）。
    

### 3. 三维场景生成 (3D Scene Generation)

将二维布局转化为可视化三维模型：

- **资源调用：** 从 **3D 物体库 (3D Object Repository)** 匹配模型。
    
- **放置与定位：** 结合物体位置信息进行空间配置。
    
- **图层渲染 (Layer Rendering)：** 对路基（Road Base）、建筑（Buildings）和家具资产（Furniture Asset）进行渲染，最终生成 **场景预览 (Scene Preview)**。
    

### 4. 生成评估 (Generation Evaluation)

对生成的场景进行质量受控的分析与优化：

- **指标评分：** 引入“完整街道指标”（Complete Street Indicators），通过 LLM 进行自动化评分。
    
- **可视化与反馈：** 进行得分可视化（Score Visualization），并由 LLM 提供改进建议（Refine Suggestions）。
    
- **反思环路 (Reflection)：** 评估结果作为“反思”信号反馈给初始 LLM，形成设计迭代。
    

---

**最终输出：** 经过优化后的全设计街道场景（Fully Designed Street Scenes）。
# References
- xcube https://github.com/nv-tlabs/XCube
	- nvidia 的 3d生成
- 