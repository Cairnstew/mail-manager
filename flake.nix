{
  description = "Hello world flake using uv2nix";

  nixConfig = {
    substituters = [
      "https://cuda-cache.cachix.org"
    ];
    trusted-public-keys = [
      "cuda-cache.cachix.org-1:me9x/YnVSDx80BvAFQLJ7naN4iDqjZqvLHSYUCPnGYY="
    ];
  };

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    # nixpkgs.url = "github:ConnorBaker/nixpkgs/feat/cudaPackages-fixed-output-derivations";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      uv2nix,
      pyproject-nix,
      pyproject-build-systems,
      ...
    }:
    let
      inherit (nixpkgs) lib;

      pyprojectData = {
        project = {
          name = "mail-manager";
          version = "0.1.0";
          description = "Generated from Nix";
          dependencies = [
              #"urllib3>=2.2.3"
              #"accelerate"
              #"einops"
              #"ema-pytorch>=0.4.2"
              #"numpy"
              #"pillow"
              #"pytorch-fid"
              #"scipy"
              #"torch>=2.0"
              #"torchvision"
              #"tqdm"
          ];
          requires-python = ">=3.12";
        };
        build-system = {
          requires = ["hatchling"];
          build-backend = "hatchling.build";
        };
      };

      generatedPyproject = (pkgs.formats.toml { }).generate "pyproject.toml" pyprojectData;

      # Load a uv workspace from a workspace root.
      # Uv2nix treats all uv projects as workspace projects.
      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };

      # Create package overlay from workspace.
      overlay = workspace.mkPyprojectOverlay {
        # Prefer prebuilt binary wheels as a package source.
        # Sdists are less likely to "just work" because of the metadata missing from uv.lock.
        # Binary wheels are more likely to, but may still require overrides for library dependencies.
        sourcePreference = "wheel"; # or sourcePreference = "sdist";
        # Optionally customise PEP 508 environment
        # environ = {
        #   platform_release = "5.10.65";
        # };
      };

      hacks = pkgs.callPackages pyproject-nix.build.hacks {};

      # Extend generated overlay with build fixups
      #
      # Uv2nix can only work with what it has, and uv.lock is missing essential metadata to perform some builds.
      # This is an additional overlay implementing build fixups.
      # See:
      # - https://pyproject-nix.github.io/uv2nix/FAQ.html
      cudaLibs = [
        pkgs.cudaPackages.cudnn
        pkgs.cudaPackages.nccl
        pkgs.cudaPackages.libcutensor
        pkgs.cudaPackages.libcusparse_lt
        pkgs.cudaPackages.libcublas
        pkgs.cudaPackages.libcusparse
        pkgs.cudaPackages.libcusolver
        pkgs.cudaPackages.libcurand
        pkgs.cudaPackages.cuda_gdb
        pkgs.cudaPackages.cuda_nvcc
        pkgs.cudaPackages.cuda_cudart
        pkgs.cudaPackages.cudatoolkit
        pkgs.cowsay
      ];

      cudaLDLibraryPath = pkgs.lib.makeLibraryPath cudaLibs;

      pyprojectOverrides = final: prev: {
        # Implement build fixups here.
        # Note that uv2nix is _not_ using Nixpkgs buildPythonPackage.
        # It's using https://pyproject-nix.github.io/pyproject.nix/build.html

        # =========================================================
        # TORCH
        # =========================================================
        torch = prev.torch.overrideAttrs (old: {
          buildInputs = (old.buildInputs or [ ]) ++ cudaLibs;
          # torch 2.11 links against cu13 nvidia wheels + nvshmem which are
          # separate packages; ignore missing and let them resolve at runtime.
          autoPatchelfIgnoreMissingDeps = true;
          postFixup = (old.postFixup or "") + ''
            addAutoPatchelfSearchPath "${final.nvidia-cublas}"
            addAutoPatchelfSearchPath "${final.nvidia-cusolver}"
            addAutoPatchelfSearchPath "${final.nvidia-cusparse}"
            addAutoPatchelfSearchPath "${final.nvidia-nvjitlink}"
            addAutoPatchelfSearchPath "${final.nvidia-cufile}"
            addAutoPatchelfSearchPath "${final.nvidia-cuda-cupti}"
            addAutoPatchelfSearchPath "${final.nvidia-cuda-nvrtc}"
            addAutoPatchelfSearchPath "${final.nvidia-cufft}"
            addAutoPatchelfSearchPath "${final.nvidia-nvshmem-cu13}"
          '';
        });
        torchvision = prev.torchvision.overrideAttrs (old: {
          buildInputs = (old.buildInputs or [ ]) ++ cudaLibs;
          postFixup = ''
            addAutoPatchelfSearchPath "${final.torch}"
          '';
        });

        # =========================================================
        # BITSANDBYTES
        # Comment out individual nvidia-* overrides below to identify
        # which ones are actually required at build time.
        # =========================================================
        bitsandbytes = prev.bitsandbytes.overrideAttrs (old: {
          buildInputs = (old.buildInputs or []) ++ cudaLibs;
          # bitsandbytes bundles backends for CUDA 11/12/13, ROCm 63/70/71/72, and XPU/SYCL.
          # Only the CUDA backend loads at runtime; ignore everything else.
          autoPatchelfIgnoreMissingDeps = true;
        });

        # =========================================================
        # NVIDIA WHEELS
        # From: grep '^name = "nvidia-' uv.lock | sort -u
        # Each entry is separate so you can comment out individually
        # to find what bitsandbytes actually needs at build time.
        # =========================================================

        # cublas - needed by torch (cu12) and bitsandbytes (cu13)
        nvidia-cublas = prev.nvidia-cublas.overrideAttrs (old: {
          buildInputs = (old.buildInputs or []) ++ cudaLibs;
          autoPatchelfIgnoreMissingDeps = true;
        });

        # cuda-cupti - needed by torch 2.11
        nvidia-cuda-cupti = prev.nvidia-cuda-cupti.overrideAttrs (old: {
          autoPatchelfIgnoreMissingDeps = true;
        });

        # cuda-nvrtc - needed by torch 2.11
        nvidia-cuda-nvrtc = prev.nvidia-cuda-nvrtc.overrideAttrs (old: {
          autoPatchelfIgnoreMissingDeps = true;
        });

        # cuda-runtime - core CUDA runtime; almost certainly needed
        # nvidia-cuda-runtime = prev.nvidia-cuda-runtime.overrideAttrs (old: {
        #   autoPatchelfIgnoreMissingDeps = true;
        # });

        # cudnn-cu13 - deep learning primitives; needed for torch/bitsandbytes ops
        # nvidia-cudnn-cu13 = prev.nvidia-cudnn-cu13.overrideAttrs (old: {
        #   autoPatchelfIgnoreMissingDeps = true;
        # });

        # cufft - needed by torch 2.11
        nvidia-cufft = prev.nvidia-cufft.overrideAttrs (old: {
          autoPatchelfIgnoreMissingDeps = true;
        });

        # cufile - GPUDirect Storage (GPU<->NVMe direct I/O); probably not needed
        nvidia-cufile = prev.nvidia-cufile.overrideAttrs (old: {
          autoPatchelfIgnoreMissingDeps = true;
        });

        # curand - GPU random number generation
        # nvidia-curand = prev.nvidia-curand.overrideAttrs (old: {
        #   autoPatchelfIgnoreMissingDeps = true;
        # });

        # cusolver - linear algebra solver; needed by bitsandbytes and torch
        nvidia-cusolver = prev.nvidia-cusolver.overrideAttrs (old: {
          buildInputs = (old.buildInputs or []) ++ cudaLibs;
          autoPatchelfIgnoreMissingDeps = true;
        });
        nvidia-cusolver-cu12 = prev.nvidia-cusolver-cu12.overrideAttrs (old: {
          buildInputs = (old.buildInputs or []) ++ cudaLibs;
          autoPatchelfIgnoreMissingDeps = true;
        });

        # cusparse - sparse linear algebra; needed by bitsandbytes and torch
        nvidia-cusparse = prev.nvidia-cusparse.overrideAttrs (old: {
          buildInputs = (old.buildInputs or []) ++ cudaLibs;
          autoPatchelfIgnoreMissingDeps = true;
        });
        nvidia-cusparse-cu12 = prev.nvidia-cusparse-cu12.overrideAttrs (old: {
          buildInputs = (old.buildInputs or []) ++ cudaLibs;
          autoPatchelfIgnoreMissingDeps = true;
        });

        # cusparselt-cu13 - sparse matrix multiply; likely needed by bitsandbytes
        # nvidia-cusparselt-cu13 = prev.nvidia-cusparselt-cu13.overrideAttrs (old: {
        #   autoPatchelfIgnoreMissingDeps = true;
        # });

        # nccl-cu13 - multi-GPU comms; needed for multi-GPU training
        # nvidia-nccl-cu13 = prev.nvidia-nccl-cu13.overrideAttrs (old: {
        #   autoPatchelfIgnoreMissingDeps = true;
        # });

        # nvjitlink - CUDA JIT linking; needed for kernel compilation
        nvidia-nvjitlink = prev.nvidia-nvjitlink.overrideAttrs (old: {
          buildInputs = (old.buildInputs or []) ++ cudaLibs;
          autoPatchelfIgnoreMissingDeps = true;
        });

        # nvshmem-cu13 - GPU OpenSHMEM for multi-node HPC; almost certainly not needed
        # Requires InfiniBand/MPI/UCX/libfabric which won't be present
        nvidia-nvshmem-cu13 = prev.nvidia-nvshmem-cu13.overrideAttrs (old: {
          autoPatchelfIgnoreMissingDeps = true;
        });

        # nvtx - profiling annotations; only needed when profiling
        # nvidia-nvtx = prev.nvidia-nvtx.overrideAttrs (old: {
        #   autoPatchelfIgnoreMissingDeps = true;
        # });

        calver = prev.calver.overrideAttrs (old: {
          postPatch = (old.postPatch or "") + ''
            if [ -f pyproject.toml ]; then
              substituteInPlace pyproject.toml --replace 'license =' '# license ='
            fi
          '';
        });

        trove-classifiers = prev.trove-classifiers.overrideAttrs (old: {
          # trove-classifiers often hits the same metadata strictness issues
          postPatch = (old.postPatch or "") + ''
            if [ -f pyproject.toml ]; then
              substituteInPlace pyproject.toml --replace 'license =' '# license ='
            fi
          '';
        });
      };


      # This example is only using x86_64-linux
      # pkgs = nixpkgs.legacyPackages.x86_64-linux;
      pkgs = import nixpkgs {
        system = "x86_64-linux";
        config.allowUnfree = true;
        config.cudaSupport = true;
      };

      # Use Python 3.12 from nixpkgs
      python = pkgs.python312;

      # Construct package set
      pythonSet =
        # Use base package set from pyproject.nix builders
        (pkgs.callPackage pyproject-nix.build.packages {
          inherit python;
        }).overrideScope
          (
            lib.composeManyExtensions [
              pyproject-build-systems.overlays.default
              overlay
              pyprojectOverrides
            ]
          );

    in
    {
      # Package a virtual environment as our main application.
      #
      # Enable no optional dependencies for production build.
      packages.x86_64-linux.default = pythonSet.mkVirtualEnv "denoising_diffusion_pytorch-env" (workspace.deps.default // {
        # torch = [ ];
      });

      # Make hello runnable with `nix run`
      apps.x86_64-linux = {
        default = self.apps.x86_64-linux.example;
        train = {
          type = "app";
          program = "${self.packages.x86_64-linux.default}/bin/train_script";
        };
        example = {
          type = "app";
          program = "${pkgs.writeShellApplication {
            name = "example-wrapper";
            runtimeInputs = [ self.packages.x86_64-linux.default ];
            text = ''
              ${./data/get_mnist.sh}
              export NCCL_P2P_DISABLE="1" NCCL_IB_DISABLE="1"
              exec train_script "$@"
            '';
            inheritPath = true;
          }}/bin/example-wrapper";
        };
      };

      # This example provides two different modes of development:
      # - Impurely using uv to manage virtual environments
      # - Pure development using uv2nix to manage virtual environments
      devShells.x86_64-linux = rec {
        default = uv2nix;

        bootstrap = pkgs.mkShell {
          name = "bootstrap-shell";
          packages = [
            pkgs.uv
            pkgs.git
            python
          ];
          env = {
            UV_PYTHON_DOWNLOADS = "never";
            UV_PYTHON = python.interpreter;
          };
          shellHook = ''
            echo "🔨 Bootstrap environment loaded."
            
            # Check if pyproject.toml already exists to avoid overwriting user changes
            if [ ! -f pyproject.toml ]; then
              echo "Generating pyproject.toml from Nix definition..."
              cat ${generatedPyproject} > pyproject.toml
              # Ensure it is writable since it came from the nix store
              chmod +w pyproject.toml
              echo "✅ pyproject.toml created."
            else
              echo "ciao! pyproject.toml already exists, skipping generation."
            fi

            if [ ! -d "src/$MODULE_NAME" ]; then
              echo "Creating src/$MODULE_NAME package..."
              mkdir -p "src/$MODULE_NAME"
              
              # Create __init__.py if missing
              if [ ! -f "src/$MODULE_NAME/__init__.py" ]; then
                cat > "src/$MODULE_NAME/__init__.py" <<EOF
          """$PROJECT_NAME package."""

          __version__ = "${pyprojectData.project.version}"
          EOF
                echo "✅ Created src/$MODULE_NAME/__init__.py"
              fi
            else
              echo "📦 src/$MODULE_NAME already exists, skipping."
            fi

            echo "Run 'uv lock' to prepare the project for uv2nix."
          '';
        };


        # It is of course perfectly OK to keep using an impure virtualenv workflow and only use uv2nix to build packages.
        # This devShell simply adds Python and undoes the dependency leakage done by Nixpkgs Python infrastructure.
        impure = pkgs.mkShell {
          packages = [
            python
            pkgs.uv
          ];
          env =
            {
              # Prevent uv from managing Python downloads
              UV_PYTHON_DOWNLOADS = "never";
              # Force uv to use nixpkgs Python interpreter
              UV_PYTHON = python.interpreter;
            }
            // lib.optionalAttrs pkgs.stdenv.isLinux {
              # Python libraries often load native shared objects using dlopen(3).
              # Setting LD_LIBRARY_PATH makes the dynamic library loader aware of libraries without using RPATH for lookup.
              LD_LIBRARY_PATH = lib.makeLibraryPath (__filter (p: p.pname != "glibc") pkgs.pythonManylinuxPackages.manylinux1);
            };
          shellHook = ''
            unset PYTHONPATH
          '';
        };

        # This devShell uses uv2nix to construct a virtual environment purely from Nix, using the same dependency specification as the application.
        # The notable difference is that we also apply another overlay here enabling editable mode ( https://setuptools.pypa.io/en/latest/userguide/development_mode.html ).
        #
        # This means that any changes done to your local files do not require a rebuild.
        #
        # Note: Editable package support is still unstable and subject to change.
        uv2nix =
          let
            # Create an overlay enabling editable mode for all local dependencies.
            editableOverlay = workspace.mkEditablePyprojectOverlay {
              # Use environment variable
              root = "$REPO_ROOT";
              # Optional: Only enable editable for these packages
              # members = [ "hello-world" ];
            };

            # Override previous set with our overrideable overlay.
            editablePythonSet = pythonSet.overrideScope (
              lib.composeManyExtensions [
                editableOverlay

                # Apply fixups for building an editable package of your workspace packages
                (final: prev: {
                  mail-manager = prev.mail-manager.overrideAttrs (old: {
                    # It's a good idea to filter the sources going into an editable build
                    # so the editable package doesn't have to be rebuilt on every change.
                    src = lib.fileset.toSource {
                      root = old.src;
                      fileset = lib.fileset.unions [
                        (old.src + "/pyproject.toml")
                        (old.src + "/README.md")
                        (old.src + "/src/mail_manager/__init__.py")
                      ];
                    };

                    # Hatchling (our build system) has a dependency on the `editables` package when building editables.
                    #
                    # In normal Python flows this dependency is dynamically handled, and doesn't need to be explicitly declared.
                    # This behaviour is documented in PEP-660.
                    #
                    # With Nix the dependency needs to be explicitly declared.
                    nativeBuildInputs =
                      old.nativeBuildInputs
                      ++ final.resolveBuildSystem {
                        editables = [ ];
                      };
                  });

                })
              ]
            );

            # Build virtual environment, with local packages being editable.
            #
            # Enable all optional dependencies for development.
            virtualenv = editablePythonSet.mkVirtualEnv "denoising_diffusion_pytorch-dev-env" workspace.deps.all;

          in
          pkgs.mkShell {
            packages = [
              virtualenv
              pkgs.uv
            ];

            env = {
              # Don't create venv using uv
              UV_NO_SYNC = "1";

              # Force uv to use Python interpreter from venv
              UV_PYTHON = "${virtualenv}/bin/python";

              # Prevent uv from downloading managed Python's
              UV_PYTHON_DOWNLOADS = "never";
            };

            shellHook = ''
              unset PYTHONPATH
              export REPO_ROOT=$(git rev-parse --show-toplevel) NCCL_P2P_DISABLE="1" NCCL_IB_DISABLE="1"
              export LD_LIBRARY_PATH=/run/opengl-driver/lib:${lib.makeLibraryPath cudaLibs}:$LD_LIBRARY_PATH
            '';
          };
      };
    };
}
