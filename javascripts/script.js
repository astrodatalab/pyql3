// Fetch GitHub Releases
const GITHUB_REPO = 'astrodatalab/pyql3';
const API_URL = `https://api.github.com/repos/${GITHUB_REPO}/releases/latest`;

document.addEventListener('DOMContentLoaded', async () => {
    const loadingEl = document.getElementById('download-loading');
    const listContainer = document.getElementById('download-buttons');
    const fallbackLink = document.getElementById('fallback-downloads');

    try {
        const response = await fetch(API_URL);
        if (!response.ok) throw new Error('Failed to fetch releases');
        
        const release = await response.json();
        const assets = release.assets;

        if (assets && assets.length > 0) {
            loadingEl.style.display = 'none';
            listContainer.style.display = 'block';
            
            assets.forEach(asset => {
                const name = asset.name.toLowerCase();
                let os = '';

                if (name.includes('macos-applesilicon') || name.includes('macos-arm64')) {
                    os = 'macOS (Apple Silicon M1/M2)';
                } else if (name.includes('macos-intel') || name.includes('macos-x86_64')) {
                    os = 'macOS (Intel)';
                } else if (name.includes('windows')) {
                    os = 'Windows (64-bit)';
                } else if (name.includes('linux')) {
                    os = 'Linux (64-bit)';
                } else {
                    os = asset.name; // Fallback to raw filename if pattern not matched
                }

                const li = document.createElement('li');
                li.style.marginBottom = '8px';
                const btn = document.createElement('a');
                btn.href = asset.browser_download_url;
                btn.innerText = `Download for ${os}`;
                btn.style.fontWeight = 'bold';
                
                li.appendChild(btn);
                listContainer.appendChild(li);
            });

        } else {
            loadingEl.innerHTML = 'No compiled binaries found for the latest release.';
            fallbackLink.style.display = 'inline';
        }
    } catch (error) {
        console.error('Error fetching GitHub releases:', error);
        loadingEl.innerHTML = 'Could not fetch latest release data.';
        fallbackLink.style.display = 'inline';
    }
});
