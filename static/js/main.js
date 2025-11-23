// 全局工具函数
const API = {
    async get(url) {
        const response = await fetch(url);
        return response.json();
    },

    async post(url, data) {
        const response = await fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data)
        });
        return response.json();
    },

    async upload(url, formData) {
        const response = await fetch(url, {
            method: 'POST',
            body: formData
        });
        return response.json();
    }
};

// 进度管理器
class ProgressManager {
    constructor() {
        this.modal = null;
        this.statusEl = null;
        this.progressBar = null;
        this.stepsEl = null;
        this.detailEl = null;
        this.steps = [];
        this.currentStep = 0;
        this.init();
    }

    init() {
        // 创建进度模态框
        const modal = document.createElement('div');
        modal.className = 'progress-modal';
        modal.innerHTML = `
            <div class="progress-content">
                <h3 id="progress-title">处理中...</h3>
                <div class="progress-status">
                    <p class="current-step" id="progress-current">准备开始...</p>
                </div>
                <div class="progress-bar-container">
                    <div class="progress-bar-fill" id="progress-bar"></div>
                </div>
                <div class="progress-steps" id="progress-steps"></div>
                <div class="progress-detail" id="progress-detail" style="display:none;"></div>
            </div>
        `;
        document.body.appendChild(modal);

        this.modal = modal;
        this.statusEl = document.getElementById('progress-current');
        this.progressBar = document.getElementById('progress-bar');
        this.stepsEl = document.getElementById('progress-steps');
        this.detailEl = document.getElementById('progress-detail');
        this.titleEl = document.getElementById('progress-title');
    }

    show(title, steps) {
        this.titleEl.textContent = title;
        this.steps = steps;
        this.currentStep = 0;
        this.modal.classList.add('active');
        this.renderSteps();
        this.updateProgress(0, steps[0]);
    }

    hide() {
        this.modal.classList.remove('active');
        this.detailEl.style.display = 'none';
        this.detailEl.textContent = '';
    }

    renderSteps() {
        this.stepsEl.innerHTML = this.steps.map((step, i) =>
            `<div class="progress-step" id="step-${i}">${step}</div>`
        ).join('');
    }

    updateProgress(step, message, detail = null) {
        this.currentStep = step;
        this.statusEl.textContent = message;

        // 更新进度条
        const progress = ((step + 1) / this.steps.length) * 100;
        this.progressBar.style.width = progress + '%';

        // 更新步骤状态
        this.steps.forEach((_, i) => {
            const stepEl = document.getElementById(`step-${i}`);
            if (i < step) {
                stepEl.className = 'progress-step completed';
            } else if (i === step) {
                stepEl.className = 'progress-step active';
            } else {
                stepEl.className = 'progress-step';
            }
        });

        // 显示详细信息
        if (detail) {
            this.detailEl.style.display = 'block';
            this.detailEl.textContent = detail;
        }
    }

    complete(message) {
        this.updateProgress(this.steps.length - 1, message);
        setTimeout(() => this.hide(), 1500);
    }

    error(message) {
        this.statusEl.textContent = '错误: ' + message;
        this.statusEl.style.color = '#c00';
    }
}

// 全局进度管理器实例
window.progressManager = new ProgressManager();

// 格式化时间
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleString('zh-CN');
}

// 错误处理
function handleError(error) {
    console.error(error);
    if (window.progressManager) {
        window.progressManager.error(error.message);
    }
    setTimeout(() => {
        alert('操作失败: ' + error.message);
    }, 100);
}

// 页面加载完成
document.addEventListener('DOMContentLoaded', () => {
    console.log('口译练习系统已加载');
});