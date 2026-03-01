**项目提案：基于“程序化布局+RAG隐式解码”的街道级离散体素生成系统**

### 一、 背景 (Background)

当前三维场景生成面临两个极端：直接生成全局连续三维场（如 NeRF/3DGS）会导致地物粘连，无法提取独立的建筑或街道家具进行算子计算；而纯程序化建模在生成微观形体时，面临巨大的人工设计成本。

本项目提出一种解耦方案：宏观层面采用程序化规则与统计学模型生成道路网与设施坐标；微观层面不再通过代码绘制形体，而是利用检索增强生成（RAG）架构，从开源三维数据集中检索预训练的隐特征向量（Latent Vector），并通过极其轻量的 3D 自动编码器（3D-VAE）将其解码为独立的体素张量。该方案能够将算力需求压缩至最低，确保在单张 16G 显存 GPU 上完成街道级场景的生成推演。

---

### 二、 数据准备与模型准备 (Data & Model Preparation)

**1. 数据准备 (非核心创新点，采用开源资源)：**

* **数据集来源**：使用开源的 Objaverse 或 ShapeNet 数据集，提取“路灯、长椅、垃圾桶、树木、建筑基础单体”等类别的 3D 网格文件（.obj/.gltf）。
* **预处理流**：将多边形网格进行空间离散化，统一转换为 $64 \times 64 \times 64$ 分辨率的二值体素张量。

**2. 模型准备 (非核心创新点，采用开源权重)：**

* **3D 自动编码器 (3D-VAE)**：提取开源模型（如 OpenAI Shape-E 的 VAE 部分）中被冻结的编码器与解码器权重。
* **向量数据库**：使用开源的 FAISS 库。在离线阶段，将所有预处理后的体素张量输入 3D-VAE 编码器，压缩为 256 维的隐向量并存入 FAISS，建立“语义标签 -> 隐向量”的索引。

---

### 三、 核心流程 (Core Workflow)

系统运行分为宏观布局计算与微观形体实例化两个节点。

#### 节点 1：宏观空间参数化布局

* **输入 (Input)**: 街道参数配置向量 $C$（包含长度 $L=500m$、设施密度 $\lambda$、用地类型比例）。
* **处理 (Role - Procedural & Statistical Engine)**: 在一维道路基准线上划分横截面。依据泊松点过程（Poisson Point Process）计算离散设施的坐标点集。
* **输出 (Output)**: 带有绝对空间坐标与语义标签的实例集合 $\mathcal{S} = \{(l_i, P_i)\}_{i=1}^N$。

#### 节点 2：微观形体 RAG 检索与解码

* **输入 (Input)**: 实例集合 $\mathcal{S}$ 中的语义标签 $l_i$。
* **处理 (Role - Vector DB & 3D Decoder)**: 从 FAISS 库中检索与 $l_i$ 对应的隐向量 $z_i$。将 $z_i$ 输入 3D-VAE 的解码器模块，执行空间上采样反卷积计算。
* **输出 (Output)**: 独立的局部高维体素张量 $V_i \in \{0, 1\}^{64 \times 64 \times 64}$。

#### 节点 3：前端组装与渲染

* **输入 (Input)**: 局部体素张量 $V_i$ 及对应的全局平移坐标 $P_i$。
* **处理 (Role - Vite & React Three Fiber Engine)**: 计算每个微观体素块的全局仿射变换矩阵，利用 InstancedMesh 进行批处理渲染。
* **输出 (Output)**: 用户可交互、可独立编辑单个对象的低保真 3D 体素场景。

---

### 四、 核心数学推导 (Core Mathematical Derivation)

本系统的核心在于将统计学分布推演的坐标与神经网络回归的几何空间进行结合。

对于节点 1 中的设施布局，设定长度为 $L$ 的道路边界内生成 $k$ 个特定设施（如长椅）的概率服从参数为 $\lambda L$ 的泊松分布：


$$P(N=k) = \frac{(\lambda L)^k e^{-\lambda L}}{k!}$$


此公式以极低的 CPU 计算代价确定了场景中对象的数量与初始坐标。

对于节点 2 中的体素解码，设检索到的连续隐向量为 $z \in \mathbb{R}^{256}$，预训练解码器网络为标量函数 $D_\theta: \mathbb{R}^{256} \to \mathbb{R}^{64 \times 64 \times 64}$。
解码器输出的是每个空间网格被占据的连续概率值 $p \in [0,1]$。为了满足物理空间的不可重叠与绝对占位原则，必须引入指示函数（Indicator Function）$\mathbb{I}$ 进行离散化坍缩。对于最终的三维张量 $V$ 中的任意坐标 $(x, y, z)$，其状态值为：


$$V_{x,y,z} = \mathbb{I}(D_\theta(z)_{x,y,z} > 0.5)$$


由此，系统将低维连续的数学特征精确映射为三维欧几里得空间中确定的离散体素边界。

---

### 五、 计算设备与预计时间 (Equipment & Timeline)

* **计算设备**：
* **开发与 Demo 演示**：单张消费级 GPU，显存 16G（如 RTX 4080 / RTX 3090/4090 移动端）。由于无需在显存中维持庞大的 3D 卷积梯度图，仅需存放解码器前向推理及轻量级 FAISS 库，16G 显存完全充足。
* **极限压力测试**：最高不超过 96G 显存的多卡服务器（用于未来验证单次生成百万级体素城区的吞吐量）。


* **预计时间**：
* 数据预处理与 FAISS 离线建库：3 天。
* Python 后端宏观管线与解码器对接开发：7 天。
* Vite 前端渲染架构与交互接口开发：4 天。
* 系统联调与性能优化：4 天。
* **总计预计周期：约 2.5 周。**



---

### 六、 预计结果 (Expected Results)

交付一个基于 B/S 架构（Browser/Server）的软件 Demo。用户输入如“500m，双向两车道，中等丰富家具，亚热带”的指令后，系统在秒级时间内于前端渲染出一条完全由微小离散方块（Minecraft 风格）构成的街道。每个建筑、路灯、树木在数据结构上均是相互正交、可独立移动或删除的张量对象，且系统预留了直接替换为高精度 GLTF 模型的标准 API。

---

### 七、 落地实现代码架构设计

#### 1. PyTorch 后端算法代码 (核心管线调度)

```python
import torch
import torch.nn as nn
import numpy as np
import faiss

# 预训练解码器定义 (极低显存占用)
class PretrainedDecoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(256, 128 * 4 * 4 * 4),
            nn.Unflatten(1, (128, 4, 4, 4)),
            nn.ConvTranspose3d(128, 64, 4, 2, 1),
            nn.ReLU(),
            nn.ConvTranspose3d(64, 32, 4, 2, 1),
            nn.ReLU(),
            nn.ConvTranspose3d(32, 16, 4, 2, 1),
            nn.ReLU(),
            nn.ConvTranspose3d(16, 1, 4, 2, 1),
            nn.Sigmoid()
        )
        
    def forward(self, z):
        prob_volume = self.net(z)
        # 严格的数学离散化过程
        voxel_tensor = (prob_volume > 0.5).to(torch.int8) 
        return voxel_tensor

class StreetGenerationPipeline:
    def __init__(self):
        # 模拟加载离线建好的向量库
        self.index = faiss.IndexFlatL2(256)
        self.decoder = PretrainedDecoder()
        self.decoder.eval()
        
    def generate_layout(self, length=500, density_lambda=0.05):
        # Process (Role: 泊松过程生成宏观坐标)
        expected_n = int(length * density_lambda)
        n_items = np.random.poisson(expected_n)
        x_coords = np.random.uniform(0, length, n_items)
        # 简化输出结构
        return [{"semantic": "bench", "pos": [x, 0, 5]} for x in x_coords]

    @torch.no_grad()
    def decode_instance(self, semantic_label):
        # Process (Role: FAISS检索与3D解码)
        # 实际代码中将根据 semantic_label 提取 query 向量，此处用随机降维向量模拟
        query_vector = np.random.randn(1, 256).astype(np.float32)
        
        # 为了Demo稳定，直接生成隐变量送入解码器
        latent_tensor = torch.from_numpy(query_vector)
        voxel_tensor = self.decoder(latent_tensor)
        
        # 提取相对坐标
        coords = torch.nonzero(voxel_tensor[0, 0]).numpy()
        return (coords - 32) * 0.1 # 中心化并缩放

    def execute_pipeline(self):
        layout = self.generate_layout()
        scene_data = []
        for item in layout:
            local_voxels = self.decode_instance(item["semantic"])
            # 计算绝对坐标
            global_voxels = local_voxels + np.array(item["pos"])
            scene_data.append({
                "id": item["semantic"],
                "voxel_coords": global_voxels.tolist()
            })
        # Output: 发送给前端的离散坐标集合
        return scene_data

```

#### 2. Vite 前端架构代码 (离散体素渲染接口)

**`src/App.jsx`:**

```jsx
import React, { useMemo } from 'react';
import { Canvas } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import * as THREE from 'three';

// Process (Role: 实例化渲染器，接收后端推演的绝对空间坐标)
const VoxelCluster = ({ voxelDataArray }) => {
  const voxelSize = 0.1;
  const geometry = useMemo(() => new THREE.BoxGeometry(voxelSize, voxelSize, voxelSize), []);
  const material = useMemo(() => new THREE.MeshStandardMaterial({ color: '#8b5a2b' }), []); // 示例颜色

  const matrices = useMemo(() => {
    const mats = [];
    const dummy = new THREE.Object3D();
    voxelDataArray.forEach(obj => {
      obj.voxel_coords.forEach(coord => {
        dummy.position.set(...coord);
        dummy.updateMatrix();
        mats.push(dummy.matrix.clone());
      });
    });
    return mats;
  }, [voxelDataArray]);

  if (matrices.length === 0) return null;

  // Output: 一次性将数万个离散体素提交给 GPU，规避性能瓶颈
  return (
    <instancedMesh args={[geometry, material, matrices.length]} castShadow>
      {matrices.map((matrix, i) => (
        <instancedBufferAttribute 
          key={i} attach="instanceMatrix" count={matrices.length} array={matrix.elements} 
        />
      ))}
    </instancedMesh>
  );
};

export default function App() {
  // Input: 此处为前端接收到的后端 execute_pipeline() 返回数据
  // 模拟两个长椅的局部体素集合
  const mockSceneData = [
    { id: 'bench', voxel_coords: [[10, 0.5, 5], [10.1, 0.5, 5], [10, 0.6, 5]] },
    { id: 'bench', voxel_coords: [[20, 0.5, 5], [20.1, 0.5, 5], [20, 0.6, 5]] }
  ];

  return (
    <div style={{ width: '100vw', height: '100vh', background: '#d3d3d3' }}>
      <Canvas camera={{ position: [15, 5, 15] }}>
        <ambientLight intensity={0.7} />
        <directionalLight position={[10, 10, 10]} />
        {/* 地面基准 */}
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[250, 0, 0]}>
          <planeGeometry args={[500, 40]} />
          <meshBasicMaterial color="#555" />
        </mesh>
        
        <VoxelCluster voxelDataArray={mockSceneData} />
        <OrbitControls target={[15, 0, 5]} />
      </Canvas>
    </div>
  );
}

```