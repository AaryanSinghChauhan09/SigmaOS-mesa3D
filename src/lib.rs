#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GpuBackend { IntelIris, AmdRadeon, NvidiaNouveau, SoftwareLlvmpipe }

#[derive(Debug)]
pub struct MesaRendererConfig {
    pub backend: GpuBackend,
    pub direct_rendering: bool,
}

impl MesaRendererConfig {
    pub fn select_backend(vendor_id: u16) -> Self {
        let backend = match vendor_id {
            0x8086 => GpuBackend::IntelIris,
            0x1002 => GpuBackend::AmdRadeon,
            0x10de => GpuBackend::NvidiaNouveau,
            _ => GpuBackend::SoftwareLlvmpipe,
        };
        let direct = backend != GpuBackend::SoftwareLlvmpipe;
        Self { backend, direct_rendering: direct }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_mesa_renderer_config_aaa() {
        // Arrange & Act
        let intel = MesaRendererConfig::select_backend(0x8086);
        let amd = MesaRendererConfig::select_backend(0x1002);
        let soft = MesaRendererConfig::select_backend(0x0000);
        // Assert
        assert_eq!(intel.backend, GpuBackend::IntelIris);
        assert!(intel.direct_rendering);
        assert_eq!(amd.backend, GpuBackend::AmdRadeon);
        assert_eq!(soft.backend, GpuBackend::SoftwareLlvmpipe);
        assert!(!soft.direct_rendering);
    }
}
